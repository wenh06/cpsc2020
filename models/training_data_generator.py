"""
"""
import os
import random
import argparse
from copy import deepcopy
from typing import Union, Optional, Any, List, Dict, NoReturn
from numbers import Real
import numpy as np
from scipy.io import loadmat, savemat
from easydict import EasyDict as ED

import misc
from cfg import PreprocessCfg, FeatureCfg
from signal_processing.ecg_preprocess import parallel_preprocess_signal
from signal_processing.ecg_features import compute_ecg_features


class CPSC2020(object):
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

    ISSUES:
    -------

    Usage:
    ------
    1. ecg arrhythmia (PVC, SPB) detection

    References:
    -----------
    [1] http://www.icbeb.org/CPSC2020.html
    [2] https://github.com/mondejar/ecg-classification
    [3] https://github.com/PIA-Group/BioSPPy
    """
    def __init__(self, db_dir:str, working_dir:Optional[str]=None, verbose:int=2, **kwargs):
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

        self._to_mv = False

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

        self.palette = {"spb": "black", "pvc": "red",}

        # NOTE:
        # the ordering of `self.allowed_preprocess` and `self.allowed_features`
        # should be in accordance with
        # corresponding items in `PreprocessCfg` and `FeatureCfg`
        self.allowed_preprocess = ['baseline', 'bandpass',]
        self.preprocess_dir = os.path.join(self.db_dir, "preprocessed")
        os.makedirs(self.preprocess_dir, exist_ok=True)
        self.rpeaks_dir = os.path.join(self.db_dir, "rpeaks")
        os.makedirs(self.rpeaks_dir, exist_ok=True)
        self.allowed_features = ['wavelet', 'rr', 'morph',]
        self.feature_dir = os.path.join(self.db_dir, "features")
        os.makedirs(self.feature_dir, exist_ok=True)
        self.beat_ann_dir = os.path.join(self.db_dir, "beat_ann")
        os.makedirs(self.beat_ann_dir, exist_ok=True)
    

    def load_data(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, keep_dim:bool=True, preprocesses:Optional[List[str]]=None, **kwargs) -> np.ndarray:
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
        preprocesses: list of str,
            type of preprocesses to perform, should be sublist of `self.allowed_preprocess`
        
        Returns:
        --------
        data: ndarray,
            the ecg data
        """
        preprocesses = self._normalize_preprocess_names(preprocesses, False)
        rec_name = self._get_rec_name(rec)
        if preprocesses:
            rec_name = f"{rec_name}-{self._get_rec_suffix(preprocesses)}"
            rec_fp = os.path.join(self.preprocess_dir, f"{rec_name}{self.rec_ext}")
        else:
            rec_fp = os.path.join(self.data_dir, f"{rec_name}{self.rec_ext}")
        data = loadmat(rec_fp)['ecg']
        if self._to_mv or kwargs.get("to_mv", False):
            data = (1000 * data).astype(int)
        sf, st = (sampfrom or 0), (sampto or len(data))
        data = data[sf:st]
        if not keep_dim:
            data = data.flatten()
        return data


    def preprocess_data(self, rec:Union[int,str], preprocesses:List[str]) -> NoReturn:
        """

        preprocesses the ecg data in advance for further use

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        preprocesses: list of str,
            type of preprocesses to perform, should be sublist of `self.allowed_preprocess`
        """
        preprocesses = self._normalize_preprocess_names(preprocesses, True)
        rec_name = self._get_rec_name(rec)
        save_fp = ED()
        save_fp.data = os.path.join(self.preprocess_dir, f"{rec_name}-{self._get_rec_suffix(preprocesses)}{self.rec_ext}")
        save_fp.rpeaks = os.path.join(self.rpeaks_dir, f"{rec_name}-{self._get_rec_suffix(preprocesses)}{self.rec_ext}")
        config = deepcopy(PreprocessCfg)
        config.preprocesses = preprocesses
        pps = parallel_preprocess_signal(self.load_data(rec, keep_dim=False), fs=self.fs, config=config)
        # save mat, keep in accordance with original mat files
        savemat(save_fp.data, {'ecg': np.atleast_2d(pps['filtered_ecg']).T}, format='5')
        savemat(save_fp.rpeaks, {'rpeaks': np.atleast_2d(pps['rpeaks']).T}, format='5')


    def compute_features(self, rec:Union[int,str], features:List[str], preprocesses:List[str], save:bool=True) -> np.ndarray:
        """

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        features: list of str,
            list of feature types to compute, should be sublist of `self.allowd_features`
        preprocesses: list of str,
            type of preprocesses to perform, should be sublist of `self.allowed_preprocess`
        save: bool, default True,
            whether or not save the features to the working directory

        Returns:
        --------
        feature_mat: ndarray,
            the computed features, of shape (m,n), where
                m = the number of beats (the number of rpeaks)
                n = the dimension of the features
        """
        features = self._normalize_feature_names(features, True)
        rec_name = self._get_rec_name(rec)
        
        try:
            print("try loading precomputed filtered signal and precomputed rpeaks...")
            data = self.load_data(rec, preprocesses=preprocesses, keep_dim=False)
            rpeaks = self.load_rpeaks(rec, preprocesses=preprocesses, keep_dim=False)
        except:
            print("no precomputed data exist")
            self.preprocess_data(rec, preprocesses=preprocesses)
            data = self.load_data(rec, preprocesses=preprocesses, keep_dim=False)
            rpeaks = self.load_rpeaks(rec, preprocesses=preprocesses, keep_dim=False)
        
        config = deepcopy(FeatureCfg)
        config.features = features
        feature_mat = compute_ecg_features(data, rpeaks, config=config)

        if save:
            save_fp = os.path.join(self.feature_dir, f"{rec_name}-{self._get_rec_suffix(preprocesses+features)}{self.rec_ext}")
            savemat(save_fp, {'features': feature_mat}, format='5')

        return feature_mat


    def load_rpeaks(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, keep_dim:bool=True, preprocesses:Optional[List[str]]=None) -> np.ndarray:
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
        
        Returns:
        --------
        rpeaks: ndarray,
            the indices of rpeaks
        """
        preprocesses = self._normalize_preprocess_names(preprocesses, True)
        rec_name = f"{self._get_rec_name(rec)}-{self._get_rec_suffix(preprocesses)}"
        rpeaks_fp = os.path.join(self.rpeaks_dir, f"{rec_name}{self.rec_ext}")
        rpeaks = loadmat(rpeaks_fp)['rpeaks'].astype(int)
        sf, st = (sampfrom or 0), (sampto or len(rpeaks))
        rpeaks = rpeaks[np.where( (rpeaks>=sf) & (rpeaks<st) )[0]]
        if not keep_dim:
            rpeaks = rpeaks.flatten()
        return rpeaks


    def load_features(self, rec:Union[int,str], features:List[str], preprocesses:Optional[List[str]]=None) -> np.ndarray:
        """

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        features: list of str,
            list of feature types computed, should be sublist of `self.allowd_features`
        preprocesses: list of str,
            type of preprocesses performed, should be sublist of `self.allowed_preprocess`
        save: bool, default False,
            whether or not save the features to the working directory

        Returns:
        --------
        feature_mat: ndarray,
            the computed features, of shape (m,n), where
                m = the number of beats (the number of rpeaks)
                n = the dimension of the features
        """
        features = self._normalize_feature_names(features, True)
        preprocesses = self._normalize_preprocess_names(preprocesses, True)
        rec_name = self._get_rec_name(rec)
        feature_fp = os.path.join(self.feature_dir, f"{rec_name}-{self._get_rec_suffix(preprocesses+features)}{self.rec_ext}")
        try:
            print("try loading precomputed features...")
            feature_mat = loadmat(feature_fp)['features']
        except FileNotFoundError:
            print("no precomputed data exist")
            feature_mat = self.compute_features(rec, features, preprocesses, save=True)
        return feature_mat


    def load_ann(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None) -> Dict[str, np.ndarray]:
        """ finished, checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        
        Returns:
        --------
        ann: dict,
            with items "SPB_indices" and "PVC_indices", which record the indices of SPBs and PVCs
        """
        ann_name = self._get_ann_name(rec)
        ann_fp = os.path.join(self.ann_dir, ann_name + self.ann_ext)
        ann = loadmat(ann_fp)['ref']
        sf, st = (sampfrom or 0), (sampto or np.inf)
        spb_indices = ann['S_ref'][0,0].flatten().astype(int)
        spb_indices = spb_indices[np.where( (spb_indices>=sf) & (spb_indices<st) )[0]]
        pvc_indices = ann['V_ref'][0,0].flatten().astype(int)
        pvc_indices = pvc_indices[np.where( (pvc_indices>=sf) & (pvc_indices<st) )[0]]
        ann = {
            "SPB_indices": spb_indices,
            "PVC_indices": pvc_indices,
        }
        return ann

    
    def load_beat_ann(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, preprocesses:Optional[List[str]]=None) -> np.ndarray:
        """
        """
        preprocesses = self._normalize_preprocess_names(preprocesses, True)
        rec_name = f"{self._get_rec_name(rec)}-{self._get_rec_suffix(preprocesses)}"
        fp = os.path.join(self.beat_ann_dir, f"{rec_name}.{self.ann_ext}")
        try:
            beat_ann = loadmat(fp)["beat_ann"]
        except FileNotFoundError:
            rpeaks = self.load_rpeaks(
                rec,
                sampfrom=sampfrom, sampto=sampto,
                keep_dim=False,
                preprocesses=preprocesses
            )
            ann = self.load_ann(rec, sampfrom, sampto)
            beat_ann = self._ann_to_beat_ann(rec, rpeaks, ann, preprocesses,)


    def _ann_to_beat_ann(self, rec:Union[int,str], rpeaks:np.ndarray, ann:Dict[str, np.ndarray], preprocesses:List[str], beat_winL:int, beat_winR:int) -> np.ndarray:
        """
        """
        beat_ann = ["N" for _ in range(len(rpeaks))]
        for idx, r in enumerate(rpeaks):
            if any([-beat_winL <= r-p < beat_winR for p in ann['SPB_indices']]):
                beat_ann[idx] = 'S'
                break
            if any([-beat_winL <= r-p < beat_winR for p in ann['PVC_indices']]):
                beat_ann[idx] = 'P'
        
        preprocesses = self._normalize_preprocess_names(preprocesses, True)
        rec_name = f"{self._get_rec_name(rec)}-{self._get_rec_suffix(preprocesses)}"
        fp = os.path.join(self.beat_ann_dir, f"{rec_name}.{self.ann_ext}")
        savemat(fp, {"rpeaks": rpeaks, "beat_ann":beat_ann}, format='5')

        return beat_ann


    def _get_ann_name(self, rec:Union[int,str]) -> str:
        """

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
        """

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
        """

        Parameters:
        -----------
        operations: list of str,
            names of operations to perform (or has performed), should be sublist of `self.allowed_preprocess` or `self.allowed_features`

        Returns:
        --------
        suffix: str,
            suffix of the filename of the preprocessed ecg signal, or the features
        """
        suffix = '-'.join(sorted([item.lower() for item in operations]))
        return suffix


    def _normalize_feature_names(self, features:List[str], ensure_nonempty:bool) -> List[str]:
        """
        """
        _f = [item.lower() for item in features] if features else []
        if ensure_nonempty:
            _f = _f or self.allowed_features
        # ensure ordering
        _f = [item for item in self.allowed_features if item in _f]
        # assert features and all([item in self.allowed_features for item in features])
        return _f


    def _normalize_preprocess_names(self, preprocesses:List[str], ensure_nonempty:bool) -> List[str]:
        """
        """
        _p = [item.lower() for item in preprocesses] if preprocesses else []
        if ensure_nonempty:
            _p = _p or self.allowed_preprocess
        # ensure ordering
        _p = [item for item in self.allowed_preprocess if item in _p]
        # assert all([item in self.allowed_preprocess for item in _p])
        return _p

    
    def plot(self, rec:Union[int,str], sampfrom:Optional[int]=None, sampto:Optional[int]=None, ectopic_beats_only:bool=False, **kwargs) -> NoReturn:
        """ not finished, not checked,

        Parameters:
        -----------
        rec: int or str,
            number of the record, NOTE that rec_no starts from 1,
            or the record name
        sampfrom: int, optional,
            start index of the data to be loaded
        sampto: int, optional,
            end index of the data to be loaded
        ectopic_beats_only: bool, default False,
            whether or not onpy plot the neighborhoods of the ectopic beats
        """
        data = self.load_data(rec, sampfrom=sampfrom, sampto=sampto, keep_dim=False)
        ann = self.load_ann(rec, sampfrom=sampfrom, sampto=sampto)
        sf, st = (sampfrom or 0), (sampto or len(data))
        if ectopic_beats_only:
            ectopic_beat_indices = sorted(ann["SPB_indices"] + ann["PVC_indices"])
            tot_interval = [sf, st]
            covering, tb = misc.get_optimal_covering(
                total_interval=tot_interval,
                to_cover=ectopic_beat_indices,
                min_len=3*self.freq,
                split_threshold=3*self.freq,
                traceback=True,
                verbose=self.verbose,
            )
        # TODO: finish plot
        raise NotImplementedError

    
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


    def train_test_split_data(self, test_rec_num:int=2, config:Optional[ED]=None) -> Tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray]:
        """ finished, checked,

        split the data (and the annotations) into train set and test set

        Parameters:
        -----------
        test_rec_num: int,
            number of records for the test set

        Returns:
        --------
        x_train, y_train, x_test, y_test: dict,
            with items `train`, `test`, both being list of record names
        """
        cfg = deepcopy(PreprocessCfg)
        cfg.update(config or {})
        split_rec = self.train_test_split_rec(test_rec_num)
        x = ED({"train": np.array([]), "test": np.array([])})
        y = ED({"train": np.array([]), "test": np.array([])})
        for subset in ["train", "test"]:
            for rec in split_rec[subset]:
                feature_mat = self.load_features(rec, features=cfg.features, preprocesses=cfg.preprocesses)
                x[subset] = np.append(x[subset], feature_mat)
                beat_ann = self.load_beat_ann(rec, preprocesses=cfgpreprocesses)
                y[subset] = np.append(y[subset], beat_ann)
        return x["train"], y["train"], x["test"], y["test"]





if __name__ == "__main__":
    from misc import dict_to_str
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
        "-p", "--preprocesses",
        type=str, default="baseline,bandpass",
        help="process to perform, separated by ','",
        dest="preprocesses",
    )
    ap.add_argument(
        "-r", "--rec",
        type=str, default=None,
        help="records (name or numbering) to perform preprocesses, separated by ','; if not set, all records will be preprocessed",
        dest="records",
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

    # data_gen = CPSC2020(db_dir="/mnt/wenhao71/data/CPSC2020/TrainingSet/")
    data_gen = CPSC2020(
        db_dir=kwargs.get("db_dir"),
        working_dir=kwargs.get("working_dir"),
        verbose=kwargs.get("verbose"),
    )
    preprocesses = kwargs.get("preprocesses", "").split(",") or PreprocessCfg.preprocesses
    for rec in (kwargs.get("records", None) or data_gen.all_records):
        data_gen.preprocess_data(rec, preprocesses=preprocesses)
