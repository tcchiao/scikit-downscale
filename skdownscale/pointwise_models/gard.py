import warnings

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.neighbors import KDTree
from sklearn.utils import check_array
from sklearn.utils.validation import check_is_fitted
from sklearn.metrics import mean_squared_error
from .utils import default_none_kwargs


def select_analogs(analogs, inds):
    # todo: this is possible with fancy indexing
    out = np.empty(len(analogs))
    for i, ind in enumerate(inds):
        out[i] = analogs[i, ind]
    return out


class AnalogBase(RegressorMixin, BaseEstimator):
    _fit_attributes = ['kdtree_', 'X_', 'y_', 'k_']

    def fit(self, X, y):
        """ Fit Analog model using a KDTree

        Parameters
        ----------
        X : pd.Series or pd.DataFrame, shape (n_samples, 1)
            Training data
        y : pd.Series or pd.DataFrame, shape (n_samples, 1)
            Target values.

        Returns
        -------
        self : returns an instance of self.
        """
        X, y = self._validate_data(X, y=y, y_numeric=True)
        self.stats_ = {}  # populated in predict methods

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


class AnalogRegression(AnalogBase):
    """ AnalogRegression

    Parameters
    ----------
    n_analogs: int
        Number of analogs to use when building linear regression
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
    """

    def __init__(
        self, 
        n_analogs=200, 
        thresh=None,
        stats=True, 
        kdtree_kwargs=None, 
        query_kwargs=None, 
        lr_kwargs=None
    ):

        self.n_analogs = n_analogs
        self.thresh = thresh
        self.stats = stats
        self.kdtree_kwargs = kdtree_kwargs
        self.query_kwargs = query_kwargs
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

        predicted = np.empty(len(X))
        errors = np.empty(len(X))

        # the next three variables are not used if self.thresh = None
        # instantiating to keep the code clean 
        exceedance_prob = np.empty(len(X))
        logistic_kwargs = default_none_kwargs(self.logistic_kwargs)
        logistic_model = LogisticRegression(**logistic_kwargs)
        
        lr_kwargs = default_none_kwargs(self.lr_kwargs)
        lr_model = LinearRegression(**lr_kwargs)

        # TODO - extract from lr_model's below.
        for i in range(len(X)):
            # predict for this time step
            predicted[i], errors[i], exceedance_prob[i] = self._predict_one_step(
                logistic_model,
                lr_model, 
                X[None, i], 
                i
            )

        out = pd.DataFrame()
        if self.thresh is not None:
            out['exceedance_prob'] = exceedance_prob
        if self.stats:
            out['error'] = errors
        out['value'] = predicted

        return out

    def _predict_one_step(self, logistic_model, lr_model, X, i, logistic_model):

        # get analogs
        query_kwargs = default_none_kwargs(self.query_kwargs)
        inds = self.kdtree_.query(X, k=self.k_, return_distance=False, **query_kwargs).squeeze()

        # extract data to train linear regression model
        x = np.asarray(self.kdtree_.data)[inds]
        y = self.y_[inds]

        # train logistic regression model 
        if self.thresh is not None:
            exceed_ind = (y > self.thresh)
            binary_y = exceed_ind.astype(int)
            logistic_model.fit(x, binary_y)
            exceedance_prob = logistic_model.predict_proba(X)
        else:
            exceed_ind = [True] * len(y)
            exceedance_prob = None

        # train linear regression model
        lr_model.fit(x[exceed_ind], y[exceed_ind])
        
        if self.stats:
            y_hat = lr_model.predict(x[exceed_ind])
            # calculate the rmse of prediction 
            error = mean_squared_error(y[exceed_ind], y_hat, squared=False)
        else:
            error = None

        # predict for this time step
        predicted = lr_model.predict(X)
        
        return predicted, error, exceedance_prob


class PureAnalog(AnalogBase):
    """ PureAnalog

    Attributes
    ----------
    kdtree_ : sklearn.neighbors.KDTree
        KDTree object
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
    """

    def __init__(
        self,
        n_analogs=200,
        kind='best_analog',
        thresh=None,
        stats=True,
        kdtree_kwargs=None,
        query_kwargs=None,
    ):
        self.n_analogs = n_analogs
        self.kind = kind
        self.thresh = thresh
        self.stats = stats
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
            masked_analogs = analogs[analog_mask]

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
            if self.thresh:
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
            exceedance_prob = np.where(analog_mask, 1, 0).mean(axis=1)

        if self.stats:
            # calculate the standard deviation of the anlogs
            if self.thresh is None:
                self.stats_['error'] = analogs.std(axis=1)
            else:
                self.stats_['error'] = analogs.where(analog_mask).std(axis=1)
                # calculate the probability of precip
                self.stats_['pop'] = exceedance_prob

        out = pd.DataFrame()
        if self.thresh is not None:
            out['exceedance_prob'] = exceedance_prob
        if self.stats:
            out['error'] = self._stats['error']
        out['value'] = predicted

        return out


class PureRegression(RegressorMixin, BaseEstimator):
    def __init__(
        self,
        thresh=None,
        stats=True,
        logistic_kwargs=None,
        linear_kwargs=None,
    ):
        self.thresh = thresh
        self.stats = stats
        self.logistic_kwargs = logistic_kwargs
        self.linear_kwargs = linear_kwargs

    def fit(self, X, y):
        if self.thresh is not None:
            exceed_ind = (y > self.thresh)
            binary_y = exceed_ind.astype(int)
            logistic_kwargs = default_none_kwargs(self.logistic_kwargs)
            self.logistic_model = LogisticRegression(**logistic_kwargs).fit(X, binary_y)
        else:
            exceed_ind = [True] * len(y)

        linear_kwargs = default_none_kwargs(self.linear_kwargs)
        self.linear_model = LinearRegression(**linear_kwargs).fit(X[exceed_ind], y[exceed_ind])

        y_hat = self.model.predict(X[exceed_ind])
        if self.stats:
            self.stats_ = {}
            error = mean_squared_error(y[exceed_ind], y_hat, squared=False)
            self.stats_['error'] = error 
        return self 

    def predict(self, X):
        out = pd.DataFrame()
        if self.thresh is not None:
            out['exceedance_prob'] = self.logistic_model.predict_proba(X)
        if self.stats:
            out['error'] = self._stats['error']
        out['value'] = self.linear_model.predict(X)
        
        return out 
