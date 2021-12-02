import warnings

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import mean_squared_error
from sklearn.neighbors import KDTree
from sklearn.utils import check_array
from sklearn.utils.validation import check_is_fitted

from .utils import default_none_kwargs


def select_analogs(analogs, inds):
    # todo: this is possible with fancy indexing
    out = np.empty(len(analogs))
    for i, ind in enumerate(inds):
        out[i] = analogs[i, ind]
    return out


class AnalogBase(RegressorMixin, BaseEstimator):
    _fit_attributes = ['kdtree_', 'X_', 'y_', 'k_', 'prediction_error_', 'exceedance_prob_']

    def fit(self, X, y):
        """ Fit Analog model using a KDTree

        Parameters
        ----------
        X : pd.Series or pd.DataFrame, shape (n_samples, n_features)
            Training data
        y : pd.Series or pd.DataFrame, shape (n_samples, 1)
            Target values.

        Returns
        -------
        self : returns an instance of self.
        """
        X, y = self._validate_data(X, y=y, y_numeric=True)
        self.prediction_error_ = []  # populated in predict methods
        self.exceedance_prob_ = []  # populated in predict methods

        if len(X) >= self.n_analogs:
            self.k_ = self.n_analogs
        else:
            warnings.warn('length of X is less than n_analogs, setting n_analogs = len(X)')
            self.k_ = len(X)

        kdtree_kwargs = default_none_kwargs(self.kdtree_kwargs)
        self.kdtree_ = KDTree(X, **kdtree_kwargs)

        self.X_ = X
        self.y_ = y

        return self

    def _more_tags(self):
        return {
            '_xfail_checks': {
                'check_dict_unchanged': 'GARD models store prediction error and exceedance probabilities during predict',
            },
        }


class AnalogRegression(AnalogBase):
    """ AnalogRegression

    Parameters
    ----------
    n_analogs: int
        Number of analogs to use when building linear regression
    thresh: float or int
        Threshold value. If provided, the model will predict:
        1) the probability of this threshold being exceeded, and
        2) the value given the threshold is exceeded
    kdtree_kwargs : dict
        Keyword arguments to pass to the sklearn.neighbors.KDTree constructor
    query_kwargs : dict
        Keyword arguments to pass to the sklearn.neighbors.KDTree.query method
    lr_kwargs : dict
        Keyword arguments to pass to the sklear.linear_model.LinearRegression
        constructor

    Attributes
    ----------
    kdtree_ : sklearn.neighbors.KDTree
        KDTree object

    Notes
    -----
    GARD models store prediction error  (`model.prediction_error_`) and
    exceedance probabilities (`model.exceedance_prob_`) for the last prediction.
    """

    def __init__(
        self,
        n_analogs=200,
        thresh=None,
        kdtree_kwargs=None,
        query_kwargs=None,
        logistic_kwargs=None,
        lr_kwargs=None,
    ):

        self.n_analogs = n_analogs
        self.thresh = thresh
        self.kdtree_kwargs = kdtree_kwargs
        self.query_kwargs = query_kwargs
        self.logistic_kwargs = logistic_kwargs
        self.lr_kwargs = lr_kwargs

    def predict(self, X):
        """ Predict using the AnalogRegression model

        Parameters
        ----------
        X : DataFrame, shape (n_samples, 1)
            Samples.

        Returns
        -------
        C : pd.DataFrame, shape (n_samples, 1)
            Returns predicted values.
        """
        # validate input data
        check_is_fitted(self)
        X = check_array(X)

        out = np.empty(len(X))

        # not used if self.thresh = None, instantiating to keep the code clean
        logistic_kwargs = default_none_kwargs(self.logistic_kwargs)
        logistic_model = LogisticRegression(**logistic_kwargs) if self.thresh is not None else None

        lr_kwargs = default_none_kwargs(self.lr_kwargs)
        lr_model = LinearRegression(**lr_kwargs)

        # TODO - extract from lr_model's below.
        self.prediction_error_ = np.zeros(len(X), dtype=np.float64)
        self.exceedance_prob_ = np.ones(len(X), dtype=np.float64)

        for i in range(len(X)):
            # predict for this time step
            out[i] = self._predict_one_step(logistic_model, lr_model, X[None, i], i,)

        return out

    def _predict_one_step(self, logistic_model, lr_model, X, i):
        # get analogs
        query_kwargs = default_none_kwargs(self.query_kwargs)
        inds = self.kdtree_.query(X, k=self.k_, return_distance=False, **query_kwargs).squeeze()

        # extract data to train linear regression model
        x = np.asarray(self.kdtree_.data)[inds]
        y = self.y_[inds]

        # figure out if there's a threshold of interest
        if self.thresh is not None:
            exceed_ind = y > self.thresh
        else:
            exceed_ind = np.ones(len(y), dtype=bool)

        # train logistic regression model
        binary_y = exceed_ind.astype(np.int8)
        if not np.all(binary_y == 1):
            logistic_model.fit(x, binary_y)
            self.exceedance_prob_[i] = logistic_model.predict_proba(X)[0, 0]
        else:
            self.exceedance_prob_[i] = 1.0

        # train linear regression model on data above threshold of interest
        lr_model.fit(x[exceed_ind], y[exceed_ind])

        # calculate the rmse of prediction
        y_hat = lr_model.predict(x[exceed_ind])
        error = mean_squared_error(y[exceed_ind], y_hat, squared=False)
        self.prediction_error_[i] = error

        predicted = lr_model.predict(X)

        return predicted


class PureAnalog(AnalogBase):
    """ PureAnalog
    Parameters
    ----------
    n_analogs : int
        Number of analogs to use
    thresh : float
        Subset analogs based on threshold
    stats : bool
        Calculate fit statistics during predict step
    kdtree_kwargs : dict
        Dictionary of keyword arguments to pass to cKDTree constructor
    query_kwargs : dict
        Dictionary of keyword arguments to pass to `cKDTree.query`

    Attributes
    ----------
    kdtree_ : sklearn.neighbors.KDTree
        KDTree object

    Notes
    -----
    GARD models store prediction error  (`model.prediction_error_`) and
    exceedance probabilities (`model.exceedance_prob_`) for the last prediction.
    """

    def __init__(
        self, n_analogs=200, kind='best_analog', thresh=None, kdtree_kwargs=None, query_kwargs=None,
    ):
        self.n_analogs = n_analogs
        self.kind = kind
        self.thresh = thresh
        self.kdtree_kwargs = kdtree_kwargs
        self.query_kwargs = query_kwargs

    def predict(self, X):
        """Predict using the PureAnalog model

        Parameters
        ----------
        X : pd.Series or pd.DataFrame, shape (n_samples, 1)
            Samples.

        Returns
        -------
        C : pd.DataFrame, shape (n_samples, 1)
            Returns predicted values.
        """
        # validate input data
        check_is_fitted(self)
        X = check_array(X)

        if self.kind == 'best_analog' or self.n_analogs == 1:
            k = 1
            kind = 'best_analog'
        else:
            k = self.k_
            kind = self.kind

        query_kwargs = default_none_kwargs(self.query_kwargs)
        dist, inds = self.kdtree_.query(X, k=k, **query_kwargs)

        analogs = np.take(self.y_, inds, axis=0)

        if self.thresh is not None:
            # TODO: rethink how the analog threshold is applied.
            # There are certainly edge cases not dealt with properly here
            # particularly in the weight analogs case
            analog_mask = analogs > self.thresh
            masked_analogs = np.where(analog_mask, analogs, np.nan)

        if kind == 'best_analog':
            predicted = analogs[:, 0]

        elif kind == 'sample_analogs':
            # get 1 random index to sample from the analogs
            rand_inds = np.random.randint(low=0, high=k, size=len(X))
            # select the analog now
            predicted = select_analogs(analogs, rand_inds)

        elif kind == 'weight_analogs':
            # take weighted average
            # work around for zero distances (perfect matches)
            tiny = 1e-20
            weights = 1.0 / np.where(dist == 0, tiny, dist)
            if self.thresh is not None:
                predicted = np.average(masked_analogs, weights=weights, axis=1)
            else:
                predicted = np.average(analogs.squeeze(), weights=weights, axis=1)

        elif kind == 'mean_analogs':
            if self.thresh is not None:
                predicted = masked_analogs.mean(axis=1)
            else:
                predicted = analogs.mean(axis=1)

        else:
            raise ValueError('got unexpected kind %s' % kind)

        if self.thresh is not None:
            # for mean/weight cases, this fills nans when all analogs
            # were below thresh
            predicted = np.nan_to_num(predicted, nan=0.0)
            self.prediction_error_ = masked_analogs.std(axis=1)
            self.exceedance_prob_ = np.where(analog_mask, 1, 0).mean(axis=1)
        else:
            self.prediction_error_ = analogs.std(axis=1)
            self.exceedance_prob_ = np.ones(len(X), dtype=np.float64)

        return predicted


class PureRegression(RegressorMixin, BaseEstimator):
    """ PureRegression
    Parameters
    ----------
    thresh : float
        Subset analogs based on threshold
    logistic_kwargs : dict
        Dictionary of keyword arguments to pass to logistic regression model
    linear_kwargs : dict
        Dictionary of keyword arguments to pass to linear regression model

    Attributes
    ----------
    kdtree_ : sklearn.neighbors.KDTree
        KDTree object

    Notes
    -----
    GARD pure regression models store prediction error  (`model.prediction_error_`) for the last fit and
    exceedance probabilities (`model.exceedance_prob_`) for the last prediction.
    """

    _fit_attributes = [
        'stats_',
        'logistic_model_',
        'linear_model_',
        'prediction_error_',
        'exceedance_prob_',
    ]

    def __init__(
        self, thresh=None, logistic_kwargs=None, linear_kwargs=None,
    ):
        self.thresh = thresh
        self.logistic_kwargs = logistic_kwargs
        self.linear_kwargs = linear_kwargs

    def fit(self, X, y):
        X, y = self._validate_data(X, y=y, y_numeric=True)
        self.stats_ = {}

        if self.thresh is not None:
            exceed_ind = y > self.thresh
            binary_y = exceed_ind.astype(np.int8)
            logistic_kwargs = default_none_kwargs(self.logistic_kwargs)
            self.logistic_model_ = LogisticRegression(**logistic_kwargs).fit(X, binary_y)
        else:
            exceed_ind = np.ones(len(y), dtype=bool)

        linear_kwargs = default_none_kwargs(self.linear_kwargs)
        self.linear_model_ = LinearRegression(**linear_kwargs).fit(X[exceed_ind], y[exceed_ind])

        y_hat = self.linear_model_.predict(X[exceed_ind])
        error = mean_squared_error(y[exceed_ind], y_hat, squared=False)
        self.prediction_error_ = np.full(shape=len(X), fill_value=error)
        self.exceedance_prob_ = []  # populated in predict

        return self

    def predict(self, X):
        check_is_fitted(self)

        if self.thresh is not None:
            self.exceedance_prob_ = self.logistic_model_.predict_proba(X)[:, 0]
        else:
            self.exceedance_prob_ = np.ones(len(np.asarray(X)), dtype=np.float64)

        return self.linear_model_.predict(X)

    def _more_tags(self):
        return {
            '_xfail_checks': {
                'check_dict_unchanged': 'GARD models store prediction error and exceedance probabilities during predict',
            },
        }
