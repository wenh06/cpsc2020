"""
"""
import os
from copy import deepcopy

import pywt
from sklearn.utils.class_weight import compute_class_weight
from easydict import EasyDict as ED

from utils import CPSC_STATS


__all__ = [
    "PreprocCfg",
    "FeatureCfg",
    "TrainCfg",
    "ModelCfg",
]


#--------------------------------------------------------------
PreprocCfg = ED()
PreprocCfg.fs = 400  # Hz, CPSC2020 data fs
# sequential, keep correct ordering, to add 'motion_artefact'
PreprocCfg.preproc = ['baseline', 'bandpass',]
# for 200 ms and 600 ms, ref. (`ecg_classification` in `reference`)
PreprocCfg.baseline_window1 = int(0.2*PreprocCfg.fs)  # 200 ms window
PreprocCfg.baseline_window2 = int(0.6*PreprocCfg.fs)  # 600 ms window
PreprocCfg.filter_band = [0.5, 45]
PreprocCfg.parallel_epoch_len = 600  # second
PreprocCfg.parallel_epoch_overlap = 10  # second
PreprocCfg.parallel_keep_tail = True
PreprocCfg.rpeaks = 'xqrs'
# or 'gqrs', or 'pantompkins', 'hamilton', 'ssf', 'christov', 'engzee', 'gamboa'
# or empty string '' if not detecting rpeaks
"""
for qrs detectors:
    `xqrs` sometimes detects s peak (valley) as r peak,
    but according to Jeethan, `xqrs` has the best performance
"""


#--------------------------------------------------------------
FeatureCfg = ED()
FeatureCfg.fs = PreprocCfg.fs  # Hz, CPSC2020 data fs
FeatureCfg.beat_winL = 100  # corr. to 250 ms
FeatureCfg.beat_winR = 100  # corr. to 250 ms
FeatureCfg.features = ['wavelet', 'rr', 'morph',]
FeatureCfg.wt_family = 'db1'
FeatureCfg.wt_level = 3
FeatureCfg.wt_feature_len = pywt.wavedecn_shapes(
    shape=(1+FeatureCfg.beat_winL+FeatureCfg.beat_winR,), 
    wavelet=FeatureCfg.wt_family,
    level=FeatureCfg.wt_level
)[0][0]
FeatureCfg.rr_local_range = 10  # 10 r peaks
FeatureCfg.rr_global_range = 5*60*FeatureCfg.fs  # 5min, units in number of points
FeatureCfg.morph_intervals = [[0,45], [85,95], [110,120], [170,200]]
FeatureCfg.class_map = dict(N=0,S=1,V=2)
FeatureCfg.beat_ann_bias_thr = 0.1*FeatureCfg.fs  # half width of broad qrs complex


#--------------------------------------------------------------
TrainCfg = ED()
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# common configurations
TrainCfg.training_data = os.path.join(_BASE_DIR, "training_data")
TrainCfg.training_workdir = os.path.join(_BASE_DIR, "training_workdir")
TrainCfg.fs = PreprocCfg.fs
TrainCfg.model_path = ED({
    "ml": os.path.join(_BASE_DIR, "models", "saved_models", "{model_name}_{params}_{scaler}_{eval}_{time}.{ext}"),
    "dl": os.path.join(_BASE_DIR, "models", "saved_models", "{model_name}_{params}_{scaler}_{eval}_{time}.{ext}"),
})
TrainCfg.model_in_use = ED({
    "ml": os.path.join(
        _BASE_DIR, "models", "saved_models",
        "XGBoost_GridSearchCV_meta.pkl"
        # "XGBClassifier_learning_rate-0.06_tree_method-gpu_hist_seed-42_StandardScaler_Test_merror_0.1426.pkl"
    ),
    "dl": os.path.join(_BASE_DIR, "models", "saved_models", ""),
})
TrainCfg.SEED = 42
TrainCfg.verbose = 1
TrainCfg.class_map = deepcopy(FeatureCfg.class_map)
TrainCfg.test_rec_num = 1
TrainCfg.augment_rpeaks = True
TrainCfg.preproc = deepcopy(PreprocCfg.preproc)
TrainCfg.class_weight = dict(N=0.018,S=1,V=0.42)  # via sklearn.utils.class_weight.compute_class_weight
# TrainCfg.class_weight = 'balanced'
TrainCfg.cv = 3
# machine learning related configuartions
TrainCfg.features = deepcopy(FeatureCfg.features)
TrainCfg.bias_thr = 0.15*TrainCfg.fs  # keep the same with `THR` in `CPSC202_score.py`
TrainCfg.feature_scaler = 'StandardScaler'  # or 'MinMaxScaler', or empty '' if not scaling features
# https://github.com/dmlc/xgboost/blob/master/doc/parameter.rst
TrainCfg.xgb_native_train_params = {
    'objective': 'multi:softmax',
    'num_class': 3,
    'verbosity': TrainCfg.verbose+1,
    'eval_metric': ['merror','mlogloss',],
    'seed': TrainCfg.SEED,
}
TrainCfg.xgb_native_train_kw = {
    'num_boost_round': 999,
    'early_stopping_rounds': 50,
    'verbose_eval': TrainCfg.verbose+1,
}
TrainCfg.xgb_native_cv_kw = {
    'num_boost_round': 999,
    'early_stopping_rounds': 20,
    'seed': TrainCfg.SEED,
    'nfold': TrainCfg.cv,
    'metrics': ['merror','mlogloss',],
    'verbose_eval': TrainCfg.verbose+1,
}
TrainCfg.ml_init_params = {
    'XGBClassifier': 'objective="multi:softmax", num_class=3, verbosity=TrainCfg.verbose+1',
    # 'SVC':,
    'RandomForestClassifier': 'class_weight="balanced", verbose=TrainCfg.verbose',
    'GradientBoostingClassifier': 'verbose=TrainCfg.verbose',
    'KNeighborsClassifier': '',
    'MLPClassifier': 'verbose=TrainCfg.verbose',
}
TrainCfg.xgbc_gpu_init_params = 'tree_method="gpu_hist", gpu_platform_id=0, gpu_device_id=0'
TrainCfg.xgbc_gpu_init_params = \
    ", ".join([TrainCfg.ml_init_params['XGBClassifier'], TrainCfg.xgbc_gpu_init_params])
TrainCfg.ml_fit_params = {
    'XGBClassifier': {  # params for xgb.train
        # 'early_stopping_rounds': 200,
        'eval_metric': ['merror',],
    },
    # 'SVC':,
    'RandomForestClassifier': {},
    'GradientBoostingClassifier': {},
    'KNeighborsClassifier': {},
    'MLPClassifier': {},
}
TrainCfg.ml_param_grid = {
    'XGBClassifier': {
        # 'objective': ['multi:softmax'],
        # 'num_classes': [3],
        "learning_rate": [0.03, 0.10, 0.30],
        'min_child_weight': [1, 5, 10],
        'gamma': [0.4, 1, 4],
        'subsample': [0.6, 1.0],
        'colsample_bytree': [0.6, 1.0],
        'max_depth': [3, 4, 6],
    },
    # 'SVC': {
    #     'C': [0.0005, 0.001, 0.002, 0.01, 0.1, 1, 10],
    #     'gamma' : [0.001, 0.01, 0.1, 1],
    #     'kernel': ['rbf', 'poly', 'sigmoid']
    # },  # might be too slow
    'RandomForestClassifier': {
        'n_estimators': [10, 40, 70, 100],
        'max_depth': [3, 5, 7],
        'min_samples_split': [0.2, 0.5, 0.7, 2],
        'min_samples_leaf': [0.2, 0.5, 1, 2],
        'max_features': [0.2, 0.5, 1, 2],
    },
    'GradientBoostingClassifier': {
        'learning_rate': [0.05, 0.1, 0.3],
        'n_estimators': [40, 70, 100],
        'subsample': [0.3, 0.5, 0.7, 1],
        'min_samples_split': [0.2, 0.5, 0.7, 2],
        'min_samples_leaf': [0.2, 0.5, 1],
        'max_depth': [3, 7],
        # 'max_features': [1, 2],
    },
    'KNeighborsClassifier': {
        'n_neighbors': [3,5,7],
        'weights': ['uniform', 'distance'],
        'p': [1, 2, 3, 4, 5],
    },
    'MLPClassifier': {
        'hidden_layer_sizes': [(200,), (300,), (400,)],
        'alpha': [0.001, 0.005, 0.01],
        'batch_size': [128, 256, 512, 1024],
        'learning_rate': ['constant', 'adaptive'],
        'max_iter': [200, 300, 400, 500],
    },
    # 'BaggingClassifier': {
    #     'n_estimators': [10, 30, 50, 60],
    #     'max_samples': [0.1, 0.3, 0.5, 0.8, 1.],
    #     'max_features': [0.2, 0.5, 1, 2],
    # },
}
# TODO: deep learning related configurations


ModelCfg = ED()
ModelCfg.torch_dtype = 'float'
