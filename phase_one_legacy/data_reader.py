"""
"""
import os
import random
import argparse
import math
from copy import deepcopy
from functools import reduce
import logging
from typing import Union, Optional, Any, List, Tuple, Dict, NoReturn
from numbers import Real

import numpy as np
import pandas as pd
from scipy.io import loadmat, savemat
import multiprocessing as mp
from easydict import EasyDict as ED

from utils import CPSC_STATS, get_optimal_covering
from cfg import BaseCfg, PreprocCfg, FeatureCfg
from signal_processing.ecg_preproc import parallel_preprocess_signal
from signal_processing.ecg_features import compute_ecg_features


__all__ = [
    "CPSC2020Reader",
]


class CPSC2020Reader(object):
    """

    The 3rd China Physiological Signal Challenge 2020:
    Searching for Premature Ventricular Contraction (PVC) and Supraventricular Premature Beat (SPB) from Long-term ECGs

    ABOUT CPSC2019:
    ---------------
    1. training data consists of 10 single-lead ECG recordings collected from arrhythmia patients, each of the recording last for about 24 hours
    2. data and annotations are stored in v5 .mat files
    3. A02, A03, A08 are patient with atrial fibrillation
    4. sampling frequency = 400 Hz
    5. Detailed information:
        -------------------------------------------------------------------------
        rec   ?AF   Length(h)   # N beats   # V beats   # S beats   # Total beats
        A01   No	25.89       109,062     0           24          109,086
        A02   Yes	22.83       98,936      4,554       0           103,490
        A03   Yes	24.70       137,249     382         0           137,631
        A04   No	24.51       77,812      19,024      3,466       100,302
        A05   No	23.57       94,614  	1	        25	        94,640
        A06   No	24.59       77,621  	0	        6	        77,627
        A07   No	23.11	    73,325  	15,150	    3,481	    91,956
        A08   Yes	25.46	    115,518 	2,793	    0	        118,311
        A09   No	25.84	    88,229  	2	        1,462	    89,693
        A10   No	23.64	    72,821	    169	        9,071	    82,061
    6. challenging factors for accurate detection of SPB and PVC:
        amplitude variation; morphological variation; noise

    NOTE:
    -----
    1. the records can roughly be classified into 4 groups:
        N:  A01, A03, A05, A06
        V:  A02, A08
        S:  A09, A10
        VS: A04, A07
    2. as premature beats and atrial fibrillation can co-exists
    (via the following code, and data from CINC2020),
    the situation becomes more complicated.
    >>> from utils.scoring_aux_data import dx_cooccurrence_all
    >>> dx_cooccurrence_all.loc["AF", ["PAC","PVC","SVPB","VPB"]]
    ... PAC     20
    ... PVC     19
    ... SVPB     4
    ... VPB     20
    ... Name: AF, dtype: int64
    this could also be seen from this dataset, via the following code as an example:
    >>> from data_reader import CPSC2020Reader as CR
    >>> db_dir = '/media/cfs/wenhao71/data/CPSC2020/TrainingSet/'
    >>> dr = CR(db_dir)
    >>> rec = dr.all_records[1]
    >>> dr.plot(rec, sampfrom=0, sampto=4000, ticks_granularity=2)

    ISSUES:
    -------
    1. currently, using `xqrs` as qrs detector,
       a lot more (more than 1000) rpeaks would be detected for A02, A07, A08,
       which might be caused by motion artefacts (or AF?);
       a lot less (more than 1000) rpeaks would be detected for A04.
       numeric details are as follows:
       ----------------------------------------------
       rec   ?AF    # beats by xqrs     # Total beats
       A01   No     109502              109,086
       A02   Yes    119562              103,490
       A03   Yes    135912              137,631
       A04   No     92746               100,302
       A05   No     94674               94,640
       A06   No     77955               77,627
       A07   No     98390               91,956
       A08   Yes    126908              118,311
       A09   No     89972               89,693
       A10   No     83509               82,061
    2. A04 has duplicate 'PVC_indices' (13534856,27147621,35141190 all appear twice):
       before correction of `load_ann`:
       >>> from collections import Counter
       >>> db_dir = "/mnt/wenhao71/data/CPSC2020/TrainingSet/"
       >>> data_gen = CPSC2020Reader(db_dir=db_dir,working_dir=db_dir)
       >>> rec = 4
       >>> ann = data_gen.load_ann(rec)
       >>> Counter(ann['PVC_indices']).most_common()[:4]
       would produce [(13534856, 2), (27147621, 2), (35141190, 2), (848, 1)]
    3. when extracting morphological features using augmented rpeaks for A04,
       `RuntimeWarning: invalid value encountered in double_scalars` would raise
       for `R_value = (R_value - y_min) / (y_max - y_min)` and
       for `y_values[n] = (y_values[n] - y_min) / (y_max - y_min)`.
       this is caused by the 13882273-th sample, which is contained in 'PVC_indices',
       however, whether it is a PVC beat, or just motion artefact, is in doubt!

    TODO:
    -----
    1. use SNR to filter out too noisy segments?
    2. for ML, consider more features

    Usage:
    ------
    1. ecg arrhythmia (PVC, SPB) detection

    References:
    -----------
    [1] http://www.icbeb.org/CPSC2020.html
    [2] https://github.com/PIA-Group/BioSPPy
    """
    def __init__(self, db_dir:str, working_dir:Optional[str]=None, verbose:int=1, **kwargs):
        """ finished, to be improved,

        Parameters:
        -----------
        db_dir: str,
            directory where the database is stored
        working_dir: str, optional,
            working directory, to store intermediate files and log file
        verbose: int, default 2,
        """
        self.db_dir = db_dir
        self.working_dir = working_dir or os.getcwd()
        self.verbose = verbose

        self.fs = 400
        self.spacing = 1000/self.fs
        self.rec_ext = '.mat'
        self.ann_ext = '.mat'

        self.nb_records = 10
        self.all_records = ["A{0:02d}".format(i) for i in range(1,1+self.nb_records)]
        self.all_annotations = ["R{0:02d}".format(i) for i in range(1,1+self.nb_records)]
        self.all_references = self.all_annotations
        self.rec_dir = os.path.join(self.db_dir, "data")
        self.ann_dir = os.path.join(self.db_dir, "ref")
        self.data_dir = self.rec_dir
        self.ref_dir = self.ann_dir

        self.subgroups = ED({
            "N":  ["A01", "A03", "A05", "A06",],
            "V":  ["A02", "A08"],
            "S":  ["A09", "A10"],
            "VS": ["A04", "A07"],
        })

        self.df_stats = CPSC_STATS

        self.palette = {"spb": "green", "pvc": "red",}

        # a dict mapping the string annotations ('N', 'S', 'V') to digits (0, 1, 2)
        self.class_map = kwargs.get("class_map", BaseCfg.class_map)

        # NOTE:
        # the ordering of `self.allowed_preproc` and `self.allowed_features`
        # should be in accordance with
        # corresponding items in `PreprocCfg` and `FeatureCfg`
        self.allowed_preproc = ['baseline', 'bandpass',]
        self.preprocess_dir = os.path.join(self.db_dir, "preprocessed")
        os.makedirs(self.preprocess_dir, exist_ok=True)
        self.rpeaks_dir = os.path.join(self.db_dir, "rpeaks")
        os.makedirs(self.rpeaks_dir, exist_ok=True)
        self.allowed_features = ['wavelet', 'rr', 'morph',]
        self.feature_dir = os.path.join(self.db_dir, "features")
        os.makedirs(self.feature_dir, exist_ok=True)
        self.beat_ann_dir = os.path.join(self.db_dir, "beat_ann")
        os.makedirs(self.beat_ann_dir, exist_ok=True)

        # TODO: add logger
    

    def load_data(self, rec:Union[int,str], units:str='mV', sampfrom:Optional[int]=None, sampto:Optional[int]=None, keep_dim:bool=True, preproc:Optional[List[str]]=None, **kwargs) -> np.ndarray:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        units: str, default 'mV',
            units of the output signal, can also be 'μV', with an alias of 'uV'
        sampfrom: int, optional,
            start index of the data to be loaded
        sampto: int, optional,
            end index of the data to be loaded
        keep_dim: bool, default True,
            whether or not to flatten the data of shape (n,1)
        preproc: list of str,
            type of preprocesses performed to the original raw data,
            should be sublist of `self.allowed_preproc`,
            if empty, the original raw data will be loaded
        
        Returns:
        --------
        data: ndarray,
            the ecg data
        """
        preproc = self._normalize_preprocess_names(preproc, False)
        rec_name = self._get_rec_name(rec)
        if preproc:
            rec_name = f"{rec_name}-{self._get_rec_suffix(preproc)}"
            rec_fp = os.path.join(self.preprocess_dir, f"{rec_name}{self.rec_ext}")
        else:
            rec_fp = os.path.join(self.data_dir, f"{rec_name}{self.rec_ext}")
        data = loadmat(rec_fp)['ecg']
        if units.lower() in ['uv', 'μv']:
            data = (1000 * data).astype(int)
        sf, st = (sampfrom or 0), (sampto or len(data))
        data = data[sf:st]
        if not keep_dim:
            data = data.flatten()
        return data


    def preprocess_data(self, rec:Union[int,str], preproc:List[str]) -> NoReturn:
        """ finished, checked,

        preprocesses the ecg data in advance for further use

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        preproc: list of str,
            type of preprocesses to perform,
            should be sublist of `self.allowed_preproc`
        """
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = self._get_rec_name(rec)
        save_fp = ED()
        save_fp.data = os.path.join(self.preprocess_dir, f"{rec_name}-{self._get_rec_suffix(preproc)}{self.rec_ext}")
        save_fp.rpeaks = os.path.join(self.rpeaks_dir, f"{rec_name}-{self._get_rec_suffix(preproc)}{self.rec_ext}")
        config = deepcopy(PreprocCfg)
        config.preproc = preproc
        pps = parallel_preprocess_signal(self.load_data(rec, keep_dim=False), fs=self.fs, config=config)
        pps['rpeaks'] = pps['rpeaks'][np.where( (pps['rpeaks']>=config.beat_winL) & (pps['rpeaks']<len(pps['filtered_ecg'])-config.beat_winR) )[0]]
        # save mat, keep in accordance with original mat files
        savemat(save_fp.data, {'ecg': np.atleast_2d(pps['filtered_ecg']).T}, format='5')
        savemat(save_fp.rpeaks, {'rpeaks': np.atleast_2d(pps['rpeaks']).T}, format='5')


    def compute_features(self, rec:Union[int,str], features:List[str], preproc:List[str], augment:bool=True, save:bool=True) -> np.ndarray:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        features: list of str,
            list of feature types to compute,
            should be sublist of `self.allowd_features`
        preproc: list of str,
            type of preprocesses to perform, should be sublist of `self.allowed_preproc`
        augment: bool, default False,
            rpeaks used for extracting features is augmented using the annotations or not
        save: bool, default True,
            whether or not save the features to the working directory

        Returns:
        --------
        feature_mat: ndarray,
            the computed features, of shape (m,n), where
                m = the number of beats (the number of rpeaks)
                n = the dimension of the features

        NOTE: for deep learning models, this function is not necessary
        """
        features = self._normalize_feature_names(features, True)
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = self._get_rec_name(rec)
        rec_name = f"{rec_name}-{self._get_rec_suffix(preproc+features)}"
        if augment:
            rec_name = rec_name + "-augment"
        
        try:
            print("try loading precomputed filtered signal and precomputed rpeaks...")
            data = self.load_data(rec, preproc=preproc, keep_dim=False)
            rpeaks = self.load_rpeaks(rec, preproc=preproc, augment=augment, keep_dim=False)
            print("precomputed filtered signal and precomputed rpeaks loaded successfully")
        except:
            print("no precomputed data exist")
            self.preprocess_data(rec, preproc=preproc)
            data = self.load_data(rec, preproc=preproc, keep_dim=False)
            rpeaks = self.load_rpeaks(rec, preproc=preproc, augment=augment, keep_dim=False)
        
        config = deepcopy(FeatureCfg)
        config.features = features
        feature_mat = compute_ecg_features(data, rpeaks, config=config)

        if save:
            save_fp = os.path.join(self.feature_dir, f"{rec_name}{self.rec_ext}")
            savemat(save_fp, {'features': feature_mat}, format='5')

        return feature_mat


    def load_rpeaks(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, keep_dim:bool=True, preproc:Optional[List[str]]=None, augment:bool=False) -> np.ndarray:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        sampfrom: int, optional,
            start index of the data to be loaded
        sampto: int, optional,
            end index of the data to be loaded
        keep_dim: bool, default True,
            whether or not to flatten the data of shape (n,1)
        preproc: list of str, optional
            preprocesses performed when detecting the rpeaks,
            should be sublist of `self.allowed_preproc`
        augment: bool, default False,
            rpeaks detected by algorithm is augmented using the annotations or not
        
        Returns:
        --------
        rpeaks: ndarray,
            the indices of rpeaks
        """
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = self._get_rec_name(rec)
        rec_name = f"{rec_name}-{self._get_rec_suffix(preproc)}"
        if augment:
            rec_name = rec_name + "-augment"
            rpeaks_fp = os.path.join(self.beat_ann_dir, f"{rec_name}{self.rec_ext}")
        else:
            rpeaks_fp = os.path.join(self.rpeaks_dir, f"{rec_name}{self.rec_ext}")
        rpeaks = loadmat(rpeaks_fp)['rpeaks'].flatten().astype(int)
        sf, st = (sampfrom or 0), (sampto or np.inf)
        rpeaks = rpeaks[np.where( (rpeaks>=sf) & (rpeaks<st) )[0]]
        if keep_dim:
            rpeaks = np.atleast_2d(rpeaks).T
        return rpeaks


    def load_features(self, rec:Union[int,str], features:List[str], preproc:Optional[List[str]], augment:bool=True, force_recompute:bool=False) -> np.ndarray:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        features: list of str,
            list of feature types computed,
            should be sublist of `self.allowd_features`
        preproc: list of str,
            type of preprocesses performed before extracting features,
            should be sublist of `self.allowed_preproc`
        augment: bool, default True,
            rpeaks used in extracting features is augmented using the annotations or not
        force_recompute: bool, default False,
            force recompute, regardless of the existing precomputed feature files

        Returns:
        --------
        feature_mat: ndarray,
            the computed features, of shape (m,n), where
                m = the number of beats (the number of rpeaks)
                n = the dimension of the features

        NOTE: for deep learning models, this function is not necessary
        """
        features = self._normalize_feature_names(features, True)
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = self._get_rec_name(rec)
        rec_name = f"{rec_name}-{self._get_rec_suffix(preproc+features)}"
        if augment:
            rec_name = rec_name + "-augment"
        feature_fp = os.path.join(self.feature_dir, f"{rec_name}{self.rec_ext}")
        if os.path.isfile(feature_fp) and not force_recompute:
            print("try loading precomputed features...")
            feature_mat = loadmat(feature_fp)['features']
            print("precomputed features loaded successfully")
        else:
            print("recompute features")
            feature_mat = self.compute_features(
                rec, features, preproc, augment, save=True
            )
        return feature_mat


    def load_ann(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None) -> Dict[str, np.ndarray]:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        sampfrom: int, optional,
            start index of the data to be loaded
        sampto: int, optional,
            end index of the data to be loaded
        
        Returns:
        --------
        ann: dict,
            with items (ndarray) "SPB_indices" and "PVC_indices",
            which record the indices of SPBs and PVCs
        """
        ann_name = self._get_ann_name(rec)
        ann_fp = os.path.join(self.ann_dir, ann_name + self.ann_ext)
        ann = loadmat(ann_fp)['ref']
        sf, st = (sampfrom or 0), (sampto or np.inf)
        spb_indices = ann['S_ref'][0,0].flatten().astype(int)
        # drop duplicates
        spb_indices = np.array(sorted(list(set(spb_indices))), dtype=int)
        spb_indices = spb_indices[np.where( (spb_indices>=sf) & (spb_indices<st) )[0]]
        pvc_indices = ann['V_ref'][0,0].flatten().astype(int)
        # drop duplicates
        pvc_indices = np.array(sorted(list(set(pvc_indices))), dtype=int)
        pvc_indices = pvc_indices[np.where( (pvc_indices>=sf) & (pvc_indices<st) )[0]]
        ann = {
            "SPB_indices": spb_indices,
            "PVC_indices": pvc_indices,
        }
        return ann

    
    def load_beat_ann(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, preproc:Optional[List[str]]=None, augment:bool=True, return_aux_data:bool=False, force_recompute:bool=False) -> Union[np.ndarray, Dict[str,np.ndarray]]:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        sampfrom: int, optional,
            start index of the data to be loaded
        sampto: int, optional,
            end index of the data to be loaded
        preproc: list of str,
            type of preprocesses performed before detecting rpeaks,
            should be sublist of `self.allowed_preproc`
        augment: bool, default True,
            rpeaks detected by algorithm is augmented using the annotations or not
        return_aux_data: bool, default False,
            whether or not return auxiliary data, including
                - the augmented rpeaks
                - the beat_ann mapped to int annotations via `self.class_map`
        force_recompute: bool, default False,
            force recompute, regardless of the existing precomputed feature files
        
        Returns:
        --------
        beat_ann: ndarray, or dict,
            annotation (one of 'N', 'S', 'V') for each beat,
            or together with auxiliary data as a dict
        """
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = f"{self._get_rec_name(rec)}-{self._get_rec_suffix(preproc)}"
        if augment:
            rec_name = rec_name + "-augment"
        fp = os.path.join(self.beat_ann_dir, f"{rec_name}{self.ann_ext}")
        if not force_recompute and os.path.isfile(fp):
            print("try loading precomputed beat_ann...")
            beat_ann = loadmat(fp)
            for k in beat_ann.keys():
                if not k.startswith("__"):
                    beat_ann[k] = beat_ann[k].flatten()
            if not return_aux_data:
                beat_ann = beat_ann["beat_ann"]
            print("precomputed beat_ann loaded successfully")
        else:
            print("recompute beat_ann")
            rpeaks = self.load_rpeaks(
                rec,
                sampfrom=sampfrom, sampto=sampto,
                keep_dim=False,
                preproc=preproc,
                augment=False,
            )
            ann = self.load_ann(rec, sampfrom, sampto)
            beat_ann = self._ann_to_beat_ann(
                rec=rec,
                rpeaks=rpeaks,
                ann=ann,
                preproc=preproc,
                bias_thr=BaseCfg.beat_ann_bias_thr,
                augment=augment,
                return_aux_data=return_aux_data,
                save=True
            )
        return beat_ann


    def _ann_to_beat_ann(self, rec:Union[int,str], rpeaks:np.ndarray, ann:Dict[str, np.ndarray], preproc:List[str], bias_thr:Real, augment:bool=True, return_aux_data:bool=False, save:bool=False) -> Union[np.ndarray, Dict[str,np.ndarray]]:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        rpeaks: ndarray,
            rpeaks for forming beats
        ann: dict,
            with items (ndarray) "SPB_indices" and "PVC_indices",
            which record the indices of SPBs and PVCs
        preproc: list of str,
            type of preprocesses performed before detecting rpeaks,
            should be sublist of `self.allowed_preproc`
        bias_thr: real number,
            tolerance for using annotations (PVC, SPB indices provided by the dataset),
            to label the type of beats given by `rpeaks`
        augment: bool, default True,
            `rpeaks` is augmented using the annotations or not
        return_aux_data: bool, default False,
            whether or not return auxiliary data, including
                - the augmented rpeaks
                - the beat_ann mapped to int annotations via `self.class_map`
        save: bool, default False,
            save the outcome beat annotations (along with 'augmented' rpeaks) to file or not
        
        Returns:
        --------
        beat_ann: ndarray, or dict,
            annotation (one of 'N', 'S', 'V') for each beat,
            or together with auxiliary data as a dict

        NOTE:
        -----
        the 'rpeaks' and 'beat_ann_int' saved in the .mat file is of shape (1,n), rather than (n,)
        """
        one_hour = self.fs*3600
        split_indices = [0]
        for i in range(1, int(rpeaks[-1]+bias_thr)//one_hour):
            split_indices.append(len(np.where(rpeaks<i*one_hour)[0])+1)
        if len(split_indices) == 1 or split_indices[-1] < len(rpeaks): # tail
            split_indices.append(len(rpeaks))

        epoch_params = []
        for idx in range(len(split_indices)-1):
            p = {}
            p['rpeaks'] = rpeaks[split_indices[idx]:split_indices[idx+1]]
            p['ann'] = {
                k: v[np.where( (v>=p['rpeaks'][0]-bias_thr-1) & (v<p['rpeaks'][-1]+bias_thr+1) )[0]] for k, v in ann.items()
            }
            # if idx == 0:
            #     p['prev_r'] = -1
            # else:
            #     p['prev_r'] = rpeaks[split_indices[idx]-1]
            # if idx == len(split_indices)-2:
            #     p['next_r'] = np.inf
            # else:
            #     p['next_r'] = rpeaks[split_indices[idx+1]]
            epoch_params.append(p)

        if augment:
            epoch_func = _ann_to_beat_ann_epoch_v3
        else:
            epoch_func = _ann_to_beat_ann_epoch_v1
        cpu_num = max(1, mp.cpu_count()-3)
        with mp.Pool(processes=cpu_num) as pool:
            result = pool.starmap(
                func=epoch_func,
                iterable=[
                    (
                        item['rpeaks'],
                        item['ann'],
                        bias_thr,
                        # item['prev_r'],
                        # item['next_r'],
                    )\
                        for item in epoch_params
                ],
            )
        ann_matched = {
            k: np.concatenate([item['ann_matched'][k] for item in result]) \
                for k in ann.keys()
        }
        ann_not_matched = {
            k: [a for a in v if a not in ann_matched[k]] for k, v in ann.items()
        }
        # print(f"rec = {rec}, ann_not_matched = {ann_not_matched}")
        beat_ann = np.concatenate([item['beat_ann'] for item in result]).astype('<U1')

        augmented_rpeaks = np.concatenate((rpeaks, np.array(ann_not_matched['SPB_indices']), np.array(ann_not_matched['PVC_indices'])))
        beat_ann = np.concatenate((beat_ann, np.array(['S' for _ in ann_not_matched['SPB_indices']], dtype='<U1'), np.array(['V' for _ in ann_not_matched['PVC_indices']], dtype='<U1')))
        sorted_indices = np.argsort(augmented_rpeaks)
        augmented_rpeaks = augmented_rpeaks[sorted_indices].astype(int)
        beat_ann = beat_ann[sorted_indices].astype('<U1')

        # NOTE: features will only be extracted at 'valid' rpeaks
        raw_sig = self.load_data(rec, keep_dim=False, preproc=None)
        valid_indices = np.where( (augmented_rpeaks>=BaseCfg.beat_winL) & (augmented_rpeaks<len(raw_sig)-BaseCfg.beat_winR) )[0]
        augmented_rpeaks = augmented_rpeaks[valid_indices]
        beat_ann = beat_ann[valid_indices]

        # list_addition = lambda a,b: a+b
        # beat_ann = reduce(list_addition, result)

        # beat_ann = ["N" for _ in range(len(rpeaks))]
        # for idx, r in enumerate(rpeaks):
        #     if any([-beat_winL <= r-p < beat_winR for p in ann['SPB_indices']]):
        #         beat_ann[idx] = 'S'
        #     elif any([-beat_winL <= r-p < beat_winR for p in ann['PVC_indices']]):
        #         beat_ann[idx] = 'V'
        
        preproc = self._normalize_preprocess_names(preproc, True)
        rec_name = f"{self._get_rec_name(rec)}-{self._get_rec_suffix(preproc)}"
        if augment:
            rec_name = rec_name + "-augment"
        fp = os.path.join(self.beat_ann_dir, f"{rec_name}{self.ann_ext}")
        to_save_mdict = {
            "rpeaks": augmented_rpeaks.astype(int),
            "beat_ann": beat_ann,
            "beat_ann_int": np.vectorize(lambda a:self.class_map[a])(beat_ann)
        }
        savemat(fp, to_save_mdict, format='5')

        if return_aux_data:
            beat_ann = to_save_mdict

        return beat_ann


    def _get_ann_name(self, rec:Union[int,str]) -> str:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name

        Returns:
        --------
        ann_name: str,
            filename of the annotation file
        """
        if isinstance(rec, int):
            assert rec in range(1, self.nb_records+1), "rec should be in range(1,{})".format(self.nb_records+1)
            ann_name = self.all_annotations[rec-1]
        elif isinstance(rec, str):
            assert rec in self.all_annotations+self.all_records, "rec should be one of {} or one of {}".format(self.all_records, self.all_annotations)
            ann_name = rec.replace("A", "R")
        return ann_name


    def _get_rec_name(self, rec:Union[int,str]) -> str:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name

        Returns:
        --------
        rec_name: str,
            filename of the record
        """
        if isinstance(rec, int):
            assert rec in range(1, self.nb_records+1), "rec should be in range(1,{})".format(self.nb_records+1)
            rec_name = self.all_records[rec-1]
        elif isinstance(rec, str):
            assert rec in self.all_records, "rec should be one of {}".format(self.all_records)
            rec_name = rec
        return rec_name


    def _get_rec_suffix(self, operations:List[str]) -> str:
        """ finished, checked,

        Parameters:
        -----------
        operations: list of str,
            names of operations to perform (or has performed),
            should be sublist of `self.allowed_preproc` or `self.allowed_features`

        Returns:
        --------
        suffix: str,
            suffix of the filename of the preprocessed ecg signal, or the features
        """
        suffix = '-'.join(sorted([item.lower() for item in operations]))
        return suffix


    def _normalize_feature_names(self, features:List[str], ensure_nonempty:bool) -> List[str]:
        """ finished, checked,

        to transform all features into lower case,
        and keep them in a specific ordering 
        
        Parameters:
        -----------
        features: list of str,
            list of feature types,
            should be sublist of `self.allowd_features`
        ensure_nonempty: bool,
            if True, when the passed `features` is empty,
            `self.allowed_features` will be returned

        Returns:
        --------
        _f: list of str,
            'normalized' list of feature types
        """
        _f = [item.lower() for item in features] if features else []
        if ensure_nonempty:
            _f = _f or self.allowed_features
        # ensure ordering
        _f = [item for item in self.allowed_features if item in _f]
        # assert features and all([item in self.allowed_features for item in features])
        return _f


    def _normalize_preprocess_names(self, preproc:List[str], ensure_nonempty:bool) -> List[str]:
        """

        to transform all preproc into lower case,
        and keep them in a specific ordering 
        
        Parameters:
        -----------
        preproc: list of str,
            list of preprocesses types,
            should be sublist of `self.allowd_features`
        ensure_nonempty: bool,
            if True, when the passed `preproc` is empty,
            `self.allowed_preproc` will be returned

        Returns:
        --------
        _p: list of str,
            'normalized' list of preprocess types
        """
        _p = [item.lower() for item in preproc] if preproc else []
        if ensure_nonempty:
            _p = _p or self.allowed_preproc
        # ensure ordering
        _p = [item for item in self.allowed_preproc if item in _p]
        # assert all([item in self.allowed_preproc for item in _p])
        return _p

    
    def train_test_split_rec(self, test_rec_num:int=2) -> Dict[str, List[str]]:
        """ finished, checked,

        split the records into train set and test set

        Parameters:
        -----------
        test_rec_num: int,
            number of records for the test set

        Returns:
        --------
        split_res: dict,
            with items `train`, `test`, both being list of record names
        """
        if test_rec_num == 1:
            test_records = random.sample(self.subgroups.VS, 1)
        elif test_rec_num == 2:
            test_records = random.sample(self.subgroups.VS, 1) + random.sample(self.subgroups.N, 1)
        elif test_rec_num == 3:
            test_records = random.sample(self.subgroups.VS, 1) + random.sample(self.subgroups.N, 2)
        elif test_rec_num == 4:
            test_records = []
            for k in self.subgroups.keys():
                test_records += random.sample(self.subgroups[k], 1)
        else:
            raise ValueError("test data ratio too high")
        train_records = [r for r in self.all_records if r not in test_records]
        
        split_res = ED({
            "train": train_records,
            "test": test_records,
        })
        
        return split_res


    def train_test_split_data(self, test_rec_num:int, features:List[str], preproc:Optional[List[str]], augment:bool=True, int_labels:bool=True) -> Tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray,np.ndarray,np.ndarray]:
        """ finished, checked,

        split the data (and the annotations) into train set and test set

        Parameters:
        -----------
        test_rec_num: int,
            number of records for the test set
        features: list of str,
            list of feature types used for producing the training data,
            should be sublist of `self.allowd_features`
        preproc: list of str,
            list of preprocesses types performed on the raw data,
            should be sublist of `self.allowd_preproc`
        augment: bool, default True,
            features are computed using augmented rpeaks or not
        int_labels: bool, default True,
            use the 'beat_ann_int', which is mapped into int via `class_map`

        Returns:
        --------
        x_train, y_train, y_indices_train, x_test, y_test, y_indices_test: ndarray,
        """
        features = self._normalize_feature_names(features, True)
        preproc = self._normalize_preprocess_names(preproc, True)
        split_rec = self.train_test_split_rec(test_rec_num)
        x = ED({"train": np.array([],dtype=float), "test": np.array([],dtype=float)})
        if int_labels:
            y = ED({"train": np.array([],dtype=int), "test": np.array([],dtype=int)})
        else:
            y = ED({"train": np.array([],dtype='<U1'), "test": np.array([],dtype='<U1')})
        y_indices = ED({"train": np.array([],dtype=int), "test": np.array([],dtype=int)})
        for subset in ["train", "test"]:
            for rec in split_rec[subset]:
                ecg_sig = self.load_data(rec, keep_dim=False, preproc=preproc)
                feature_mat = self.load_features(
                    rec,
                    features=features,
                    preproc=preproc,
                    augment=augment,
                    force_recompute=False
                )
                beat_ann = self.load_beat_ann(
                    rec,
                    preproc=preproc,
                    augment=augment,
                    return_aux_data=True,
                    force_recompute=False
                )
                # NOTE: the following has been moved to the function `_ann_to_beat_ann`
                # valid_indices = np.where( (beat_ann["rpeaks"].ravel()>=BaseCfg.beat_winL) & (beat_ann["rpeaks"].ravel()<len(ecg_sig)-BaseCfg.beat_winR) )[0]
                # feature_mat = feature_mat[valid_indices]
                # beat_ann["beat_ann"] = beat_ann["beat_ann"][valid_indices]
                if len(x[subset]):
                    x[subset] = np.concatenate((x[subset], feature_mat), axis=0)
                else:
                    x[subset] = feature_mat.copy()
                if int_labels:
                    y[subset] = np.append(y[subset], beat_ann["beat_ann_int"].astype(int))
                else:
                    y[subset] = np.append(y[subset], beat_ann["beat_ann"])
                y_indices[subset] = np.append(y_indices[subset], beat_ann["rpeaks"]).astype(int)
            # post process: drop invalid (nan, inf, etc.)
            invalid_indices = list(set(np.where(~np.isfinite(x[subset]))[0]))
            x[subset] = np.delete(x[subset], invalid_indices, axis=0)
            y[subset] = np.delete(y[subset], invalid_indices)
            y_indices[subset] = np.delete(y_indices[subset], invalid_indices)
        return x["train"], y["train"], y_indices["train"], x["test"], y["test"], y_indices["test"]


    def locate_premature_beats(self, rec:Union[int,str], premature_type:Optional[str]=None, window:int=10000, sampfrom:Optional[int]=None, sampto:Optional[int]=None) -> List[List[int]]:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        premature_type: str, optional,
            premature beat type, can be one of "SPB", "PVC"
        window: int, default 10000,
            window length of each premature beat
        sampfrom: int, optional,
            start index of the premature beats to locate
        sampto: int, optional,
            end index of the premature beats to locate

        Returns:
        --------
        premature_intervals: list,
            list of intervals of premature beats
        """
        ann = self.load_ann(rec)
        if premature_type:
            premature_inds = ann[f"{premature_type.upper()}_indices"]
        else:
            premature_inds = np.append(ann["SPB_indices"], ann["PVC_indices"])
            premature_inds = np.sort(premature_inds)
        try:  # premature_inds empty?
            sf, st = (sampfrom or 0), (sampto or premature_inds[-1]+1)
        except:
            premature_intervals = []
            return premature_intervals
        premature_inds = premature_inds[(sf < premature_inds) & (premature_inds < st)]
        tot_interval = [sf, st]
        premature_intervals, _ = get_optimal_covering(
            total_interval=tot_interval,
            to_cover=premature_inds,
            min_len=window*self.fs//1000,
            split_threshold=window*self.fs//1000,
            traceback=False,
        )
        return premature_intervals


    def _auto_infer_units(self, sig:np.ndarray, sig_type:str="ECG") -> str:
        """ finished, checked,

        automatically infer the units of `data`,
        under the assumption that `data` not raw data, with baseline removed

        Parameters:
        -----------
        sig: ndarray,
            the signal to infer its units
        sig_type: str, default "ECG", case insensitive,
            type of the signal

        Returns:
        --------
        units: str,
            units of `data`, 'μV' or 'mV'
        """
        if sig_type.lower() == "ecg":
            _MAX_mV = 20  # 20mV, seldom an ECG device has range larger than this value
            max_val = np.max(np.abs(data))
            if max_val > _MAX_mV:
                units = 'μV'
            else:
                units = 'mV'
        else:
            raise NotImplementedError(f"not implemented for {sig_type}")
        return units

    
    def plot(self, rec:Union[int,str], data:Optional[np.ndarray]=None, ticks_granularity:int=0, sampfrom:Optional[int]=None, sampto:Optional[int]=None) -> NoReturn:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        data: ndarray, optional,
            ecg signal to plot,
            if given, data of `rec` will not be used,
            this is useful when plotting filtered data
        ticks_granularity: int, default 0,
            the granularity to plot axis ticks, the higher the more,
            0 (no ticks) --> 1 (major ticks) --> 2 (major + minor ticks)
        sampfrom: int, optional,
            start index of the data to plot
        sampto: int, optional,
            end index of the data to plot
        """
        if 'plt' not in dir():
            import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        patches = {}

        if data is None:
            _data = self.load_data(
                rec, units="μV", sampfrom=sampfrom, sampto=sampto, keep_dim=False
            )
        else:
            units = self._auto_infer_units(data)
            if units == "mV":
                _data = data * 1000
            elif units == "μV":
                _data = data.copy()

        ann = self.load_ann(rec, sampfrom=sampfrom, sampto=sampto)
        sf, st = (sampfrom or 0), (sampto or len(_data))
        spb_indices = ann["SPB_indices"]
        pvc_indices = ann["PVC_indices"]
        spb_indices = spb_indices - sf
        pvc_indices = pvc_indices - sf

        line_len = self.fs * 25  # 25 seconds
        nb_lines = math.ceil(len(_data)/line_len)

        for idx in range(nb_lines):
            seg = _data[idx*line_len: (idx+1)*line_len]
            secs = (np.arange(len(seg)) + idx*line_len) / self.fs
            fig_sz_w = int(round(4.8 * len(seg) / self.fs))
            y_range = np.max(np.abs(seg)) + 100
            fig_sz_h = 6 * y_range / 1500
            fig, ax = plt.subplots(figsize=(fig_sz_w, fig_sz_h))
            ax.plot(secs, seg, c='black')
            ax.axhline(y=0, linestyle='-', linewidth='1.0', color='red')
            if ticks_granularity >= 1:
                ax.xaxis.set_major_locator(plt.MultipleLocator(0.2))
                ax.yaxis.set_major_locator(plt.MultipleLocator(500))
                ax.grid(which='major', linestyle='-', linewidth='0.5', color='red')
            if ticks_granularity >= 2:
                ax.xaxis.set_minor_locator(plt.MultipleLocator(0.04))
                ax.yaxis.set_minor_locator(plt.MultipleLocator(100))
                ax.grid(which='minor', linestyle=':', linewidth='0.5', color='black')
            seg_spb = np.where( (spb_indices>=idx*line_len) & (spb_indices<(idx+1)*line_len) )[0]
            # print(f"spb_indices = {spb_indices}, seg_spb = {seg_spb}")
            if len(seg_spb) > 0:
                seg_spb = spb_indices[seg_spb] / self.fs
                patches["SPB"] = mpatches.Patch(color=self.palette["spb"], label="SPB")
            seg_pvc = np.where( (pvc_indices>=idx*line_len) & (pvc_indices<(idx+1)*line_len) )[0]
            # print(f"pvc_indices = {pvc_indices}, seg_pvc = {seg_pvc}")
            if len(seg_pvc) > 0:
                seg_pvc = pvc_indices[seg_pvc] / self.fs
                patches["PVC"] = mpatches.Patch(color=self.palette["pvc"], label="PVC")
            for t in seg_spb:
                ax.axvspan(
                    max(secs[0], t-0.05), min(secs[-1], t+0.05),
                    color=self.palette["spb"], alpha=0.5
                )
            for t in seg_pvc:
                ax.axvspan(
                    max(secs[0], t-0.05), min(secs[-1], t+0.05),
                    color=self.palette["pvc"], alpha=0.5
                )
            if len(patches) > 0:
                ax.legend(
                    handles=[v for _,v in patches.items()],
                    loc="lower left",
                    prop={"size": 16}
                )
            ax.set_xlim(secs[0], secs[-1])
            ax.set_ylim(-y_range, y_range)
            ax.set_xlabel('Time [s]')
            ax.set_ylabel('Voltage [μV]')
            plt.show()


def _ann_to_beat_ann_epoch_v1(rpeaks:np.ndarray, ann:Dict[str, np.ndarray], bias_thr:Real) -> dict:
    """ finished, checked,

    the naive method to label beat types using annotations provided by the dataset
    
    Parameters:
    -----------
    rpeaks: ndarray,
        rpeaks for forming beats
    ann: dict,
        with items (ndarray) "SPB_indices" and "PVC_indices",
        which record the indices of SPBs and PVCs
    bias_thr: real number,
        tolerance for using annotations (PVC, SPB indices provided by the dataset),
        to label the type of beats given by `rpeaks`

    Returns:
    --------
    retval: dict, with the following items
        - ann_matched: dict of ndarray,
            indices of annotations ("SPB_indices" and "PVC_indices")
            that match some beat from `rpeaks`.
            for v1, this term is always the same as `ann`, hence useless
        - beat_ann: ndarray,
            label for each beat from `rpeaks`
    """
    beat_ann = np.array(["N" for _ in range(len(rpeaks))])
    for idx, r in enumerate(rpeaks):
        if any([abs(r-p) < bias_thr for p in ann['SPB_indices']]):
            beat_ann[idx] = 'S'
        elif any([abs(r-p) < bias_thr for p in ann['PVC_indices']]):
            beat_ann[idx] = 'V'
    ann_matched = ann.copy()
    retval = dict(ann_matched=ann_matched, beat_ann=beat_ann)
    return retval

@DeprecationWarning
def _ann_to_beat_ann_epoch_v2(rpeaks:np.ndarray, ann:Dict[str, np.ndarray], bias_thr:Real) -> dict:
    """ finished, checked, has flaws, deprecated,

    similar to `_ann_to_beat_ann_epoch_v1`, but records those matched annotations,
    for further post-process, adding those beats that are in annotation,
    but not detected by the signal preprocessing algorithms (qrs detection)

    however, the comparison process (the block inside the outer `for` loop)
    is not quite correct
    
    Parameters:
    -----------
    rpeaks: ndarray,
        rpeaks for forming beats
    ann: dict,
        with items (ndarray) "SPB_indices" and "PVC_indices",
        which record the indices of SPBs and PVCs
    bias_thr: real number,
        tolerance for using annotations (PVC, SPB indices provided by the dataset),
        to label the type of beats given by `rpeaks`

    Returns:
    --------
    retval: dict, with the following items
        - ann_matched: dict of ndarray,
            indices of annotations ("SPB_indices" and "PVC_indices")
            that match some beat from `rpeaks`
        - beat_ann: ndarray,
            label for each beat from `rpeaks`
    """
    beat_ann = np.array(["N" for _ in range(len(rpeaks))], dtype='<U1')
    # used to add back those beat that is not detected via proprocess algorithm
    _ann = {k: v.astype(int).tolist() for k,v in ann.items()}
    for idx_r, r in enumerate(rpeaks):
        found = False
        for idx_a, a in enumerate(_ann['SPB_indices']):
            if abs(r-a) < bias_thr:
                found = True
                beat_ann[idx_r] = 'S'
                del _ann['SPB_indices'][idx_a]
                break
        if found:
            continue
        for idx_a, a in enumerate(_ann['PVC_indices']):
            if abs(r-a) < bias_thr:
                found = True
                beat_ann[idx_r] = 'V'
                del _ann['PVC_indices'][idx_a]
                break
    ann_matched = {
        k: np.array([a for a in v if a not in _ann[k]], dtype=int) for k,v in ann.items()
    }
    retval = dict(ann_matched=ann_matched, beat_ann=beat_ann)
    return retval
    # _ann['SPB_indices'] = [a for a in _ann['SPB_indices'] if prev_r<a<next_r]
    # _ann['PVC_indices'] = [a for a in _ann['PVC_indices'] if prev_r<a<next_r]
    # augmented_rpeaks = np.concatenate((rpeaks, np.array(_ann['SPB_indices']), np.array(_ann['PVC_indices'])))
    # beat_ann = np.concatenate((beat_ann, np.array(['S' for _ in _ann['SPB_indices']], dtype='<U1'), np.array(['V' for _ in _ann['PVC_indices']], dtype='<U1')))
    # sorted_indices = np.argsort(augmented_rpeaks)
    # augmented_rpeaks = augmented_rpeaks[sorted_indices].astype(int)
    # beat_ann = beat_ann[sorted_indices].astype('<U1')

    # retval = dict(augmented_rpeaks=augmented_rpeaks, beat_ann=beat_ann)
    # return retval

def _ann_to_beat_ann_epoch_v3(rpeaks:np.ndarray, ann:Dict[str, np.ndarray], bias_thr:Real) -> dict:
    """ finished, checked,
    
    similar to `_ann_to_beat_ann_epoch_v2`, but more reasonable
    
    Parameters:
    -----------
    rpeaks: ndarray,
        rpeaks for forming beats
    ann: dict,
        with items (ndarray) "SPB_indices" and "PVC_indices",
        which record the indices of SPBs and PVCs
    bias_thr: real number,
        tolerance for using annotations (PVC, SPB indices provided by the dataset),
        to label the type of beats given by `rpeaks`

    Returns:
    --------
    retval: dict, with the following items
        - ann_matched: dict of ndarray,
            indices of annotations ("SPB_indices" and "PVC_indices")
            that match some beat from `rpeaks`
        - beat_ann: ndarray,
            label for each beat from `rpeaks`
    """
    beat_ann = np.array(["N" for _ in range(len(rpeaks))], dtype='<U1')
    ann_matched = {k: [] for k,v in ann.items()}
    for idx_r, r in enumerate(rpeaks):
        dist_to_spb = np.abs(r-ann["SPB_indices"])
        dist_to_pvc = np.abs(r-ann["PVC_indices"])
        if len(dist_to_spb) == 0:
            dist_to_spb = np.array([np.inf])
        if len(dist_to_pvc) == 0:
            dist_to_pvc = np.array([np.inf])
        argmin = np.argmin([np.min(dist_to_spb), np.min(dist_to_pvc), bias_thr])
        if argmin == 2:
            pass
        elif argmin == 1:
            beat_ann[idx_r] = "V"
            ann_matched["PVC_indices"].append(ann["PVC_indices"][np.argmin(dist_to_pvc)])
        elif argmin == 0:
            beat_ann[idx_r] = "S"
            ann_matched["SPB_indices"].append(ann["SPB_indices"][np.argmin(dist_to_spb)])
    ann_matched = {k: np.array(v) for k,v in ann_matched.items()}
    retval = dict(ann_matched=ann_matched, beat_ann=beat_ann)
    return retval



if __name__ == "__main__":
    from .utils import dict_to_str, str2bool
    ap = argparse.ArgumentParser(
        description="preprocess CPSC2020 data",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "-d", "--db-dir",
        type=str, required=True,
        help="directory where the database is stored",
        dest="db_dir",
    )
    ap.add_argument(
        "-w", "--working-dir",
        type=str, default=None,
        help="working directory",
        dest="working_dir",
    )
    ap.add_argument(
        "-p", "--preproc",
        type=str, default="baseline,bandpass",
        help="preprocesses to perform, separated by ','",
        dest="preproc",
    )
    ap.add_argument(
        "-f", "--features",
        type=str, default="wavelet,rr,morph",
        help="features to extract, separated by ','",
        dest="features",
    )
    ap.add_argument(
        "-r", "--rec",
        type=str, default=None,
        help="records (name or numbering) to perform preprocesses, separated by ','; if not set, all records will be preprocessed",
        dest="records",
    )
    ap.add_argument(
        "-a", "--augment",
        type=str2bool, default=True,
        help="whether or not using annotations to augment the rpeaks detected by algorithm",
        dest="augment",
    )
    ap.add_argument(
        "-v", "--verbose",
        type=int, default=2,
        help="verbosity",
        dest="verbose",
    )
    # TODO: add more args

    kwargs = vars(ap.parse_args())
    print("passed arguments:")
    print(f"{dict_to_str(kwargs)}")

    # data_gen = CPSC2020Reader(db_dir="/mnt/wenhao71/data/CPSC2020/TrainingSet/")
    data_gen = CPSC2020Reader(
        db_dir=kwargs.get("db_dir"),
        working_dir=kwargs.get("working_dir"),
        verbose=kwargs.get("verbose"),
    )

    preproc = kwargs.get("preproc", "").split(",") or PreprocCfg.preproc
    features = kwargs.get("features", "").split(",") or PreprocCfg.preproc
    augment = kwargs.get("augment", True)

    for rec in (kwargs.get("records", None) or data_gen.all_records):
        data_gen.preprocess_data(
            rec,
            preproc=preproc,
        )
        data_gen.compute_features(
            rec,
            features=features,
            preproc=preproc,
            augment=augment,
            save=True,
        )
        data_gen.load_beat_ann(
            rec,
            preproc=preproc,
            augment=augment,
        )
