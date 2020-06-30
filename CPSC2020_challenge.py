"""
"""
import numpy as np

from ecg_preprocess import preprocess_signal, parallel_preprocess_signal


def CPSC2020_challenge(ECG, fs):
    """
    % This function can be used for events 1 and 2. Participants are free to modify any
    % components of the code. However the function prototype must stay the same
    % [S_pos,V_pos] = CPSC2020_challenge(ECG,fs) where the inputs and outputs are specified
    % below.
    %
    %% Inputs
    %       ECG : raw ecg vector signal 1-D signal
    %       fs  : sampling rate
    %
    %% Outputs
    %       S_pos : the position where SPBs detected
    %       V_pos : the position where PVCs detected
    %
    %
    %
    % Copyright (C) 2020 Dr. Chengyu Liu
    % Southeast university
    % chengyu@seu.edu.cn
    %
    % Last updated : 02-23-2020

    """

#   ====== arrhythmias detection =======

#    S_pos = np.zeros([1, ])
#    V_pos = np.zeros([1, ])
    pr = parallel_preprocess_signal(ECG, fs)
    filtered_ecg = pr['filtered_ecg']
    rpeaks = pr['rpeaks']
    
    raise NotImplementedError
    return S_pos, V_pos
