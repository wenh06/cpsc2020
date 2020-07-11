"""
algorithms detecting R peaks from (filtered) ECG signal
"""
from numbers import Real
from typing import Union, Optional

import numpy as np

from wfdb.processing.qrs import XQRS, GQRS, xqrs_detect as _xqrs_detect, gqrs_detect as _gqrs_detect
from wfdb.processing.pantompkins import pantompkins as _pantompkins
try:
    import biosppy.signals.ecg as BSE
except:
    import references.biosppy.biosppy.signals.ecg as BSE


__all__ = [
    "xqrs_detect", "gqrs_detect", "pantompkins",
    "hamilton_detect", "ssf_detect", "christov_detect", "engzee_detect", "gamboa_detect",
]

# ---------------------------------------------------------------------
# algorithms from wfdb
def pantompkins(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """ to keep in accordance of parameters with `xqrs` and `gqrs` """
    rpeaks = _pantompkins(sig, fs)
    return rpeaks

def xqrs_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """
    default kwargs:
        sampfrom=0, sampto='end', conf=None, learn=True, verbose=True
    """
    kw = dict(sampfrom=0, sampto='end', conf=None, learn=True, verbose=True)
    kw = {k: kwargs.get(k,v) for k,v in kw.items()}
    rpeaks = _xqrs_detect(sig, fs, **kw)
    return rpeaks

def gqrs_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """
    default kwargs:
        d_sig=None, adc_gain=None, adc_zero=None,
        threshold=1.0, hr=75, RRdelta=0.2, RRmin=0.28, RRmax=2.4,
        QS=0.07, QT=0.35, RTmin=0.25, RTmax=0.33,
        QRSa=750, QRSamin=130
    """
    kw = dict(d_sig=None, adc_gain=None, adc_zero=None,
        threshold=1.0, hr=75, RRdelta=0.2, RRmin=0.28, RRmax=2.4,
        QS=0.07, QT=0.35, RTmin=0.25, RTmax=0.33,
        QRSa=750, QRSamin=130
    )
    kw = {k: kwargs.get(k,v) for k,v in kw.items()}
    rpeaks = _gqrs_detect(sig, fs, **kw)
    return rpeaks


# ---------------------------------------------------------------------
# algorithms from biosppy
def hamilton_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """

    References:
    -----------
    [1] P.S. Hamilton, "Open Source ECG Analysis Software Documentation", E.P.Limited, 2002
    """
    # segment
    rpeaks, = BSE.hamilton_segmenter(signal=sig, sampling_rate=fs)

    # correct R-peak locations
    rpeaks, = BSE.correct_rpeaks(
        signal=sig,
        rpeaks=rpeaks,
        sampling_rate=fs,
        tol=kwargs.get("correct_tol", 0.05),
    )
    return rpeaks

def ssf_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """

    Slope Sum Function (SSF)

    References:
    -----------
    [1]
    """
    rpeaks, = BSE.ssf_segmenter(
        signal=sig, sampling_rate=fs,
        threshold=kwargs.get("threshold", 20),
        before=kwargs.get("before", 0.03),
        after=kwargs.get("after", 0.01),
    )
    # correct R-peak locations
    rpeaks, = BSE.correct_rpeaks(
        signal=sig,
        rpeaks=rpeaks,
        sampling_rate=fs,
        tol=kwargs.get("correct_tol", 0.05),
    )
    return rpeaks

def christov_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """

    References:
    -----------
    [1] Ivaylo I. Christov, "Real time electrocardiogram QRS detection using combined adaptive threshold", BioMedical Engineering OnLine 2004, vol. 3:28, 2004
    """
    rpeaks, = BSE.christov_segmenter(signal=sig, sampling_rate=fs)
    # correct R-peak locations
    rpeaks, = BSE.correct_rpeaks(
        signal=sig,
        rpeaks=rpeaks,
        sampling_rate=fs,
        tol=kwargs.get("correct_tol", 0.05),
    )
    return rpeaks

def engzee_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """

    References:
    -----------
    [1] W. Engelse and C. Zeelenberg, "A single scan algorithm for QRS detection and feature extraction", IEEE Comp. in Cardiology, vol. 6, pp. 37-42, 1979
    [2] A. Lourenco, H. Silva, P. Leite, R. Lourenco and A. Fred, "Real Time Electrocardiogram Segmentation for Finger Based ECG Biometrics", BIOSIGNALS 2012, pp. 49-54, 2012
    """
    rpeaks, = BSE.engzee_segmenter(
        signal=sig, sampling_rate=fs,
        threshold=kwargs.get("threshold", 0.48),
    )
    # correct R-peak locations
    rpeaks, = BSE.correct_rpeaks(
        signal=sig,
        rpeaks=rpeaks,
        sampling_rate=fs,
        tol=kwargs.get("correct_tol", 0.05),
    )
    return rpeaks

def gamboa_detect(sig:np.ndarray, fs:Real, **kwargs) -> np.ndarray:
    """

    References:
    -----------
    [1] 
    """
    rpeaks, = BSE.gamboa_segmenter(
        signal=sig, sampling_rate=fs,
        tol=kwargs.get("tol", 0.48),
    )
    # correct R-peak locations
    rpeaks, = BSE.correct_rpeaks(
        signal=sig,
        rpeaks=rpeaks,
        sampling_rate=fs,
        tol=kwargs.get("correct_tol", 0.05),
    )
    return rpeaks
