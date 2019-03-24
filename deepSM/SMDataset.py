import os

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader, ConcatDataset, Sampler

from deepSM.SMData import SMFile
from deepSM import utils
import deepSM.beat_time_converter as BTC
from deepSM import wavutils
from deepSM import StepPlacement

import h5py

from importlib import reload
reload(BTC)
reload(wavutils)
reload(utils)

__version__ = '2-0-0'

class SMDataset(Dataset):
    """
    Dataset loader for note placement network.
    Loads and feature engineers the songs and sm files for training.

    Note: Frame context size is currently hard coded!

    For chunk size:
        Use None for Conv models.
        Use -1 for prediction (Returns entire song as a single sequence.)
        Use N for RNNs of sequence length N.
    """

    def __init__(self, song_name, fft_features, step_pos_labels,
            step_type_labels, diffs, chunk_size, context_size,
            step_predictions=None):

        # Dataset properties.
        self.song_name = song_name
        self.fft_features = fft_features
        self.step_pos_labels = step_pos_labels
        self.step_type_labels = step_type_labels
        self.diffs = diffs

        # May be null.
        if step_predictions is not None:
            assert isinstance(step_predictions, np.ndarray)

            # Apply sigmoid.
            self.step_predictions = 1 / (1 + np.exp(-step_predictions))
        else:
            self.step_predictions = None

        self.N_frames = fft_features.shape[1]
        self.context_size = int(context_size)


        # Parse chunk size.
        self.conv = False
        if chunk_size is None:
            # Used for conv models.
            self.conv = True
            self.chunk_size = 1

        elif chunk_size > 0:
            self.chunk_size = int(chunk_size)

        elif chunk_size == -1:
            # Get maximum chunk size, ie. sample_length == 1
            self.chunk_size = self.N_frames - self.context_size * 2
        else:
            raise ValueError("Invalid chunk size.")


        # Genratable from dataset properties.
        self.sample_length = self.N_frames \
                - self.chunk_size - self.context_size * 2 + 1



    def __len__(self):
        # Can start at any point in the song, as long as there is enough
        # room to unroll to chunk_size.
        return len(self.diffs) * self.sample_length


    def __getitem__(self, idx):
        # Since all difficulties have the same number of frames, divide to get
        # which diff, order determined by self.diffs.
        # Remainder to find the frame.
        # "Concatenated" representation.
        diff_idx = idx // self.sample_length
        frame_idx = idx % self.sample_length


        diff = self.diffs[diff_idx]
        diff_code = utils.difficulties[diff]

        window_size = self.context_size * 2 + 1

        fft_slice = slice(frame_idx, frame_idx + self.chunk_size + window_size-1)
        window_slice = slice(frame_idx + self.context_size,
                frame_idx + self.context_size + self.chunk_size)

        feature_window = torch.from_numpy(self.fft_features[:,fft_slice,:])

        fft_features = feature_window.unfold(1, window_size, 1)
        fft_features = fft_features.transpose(2, 3).transpose(0, 1)


        diff_mtx = np.zeros((self.chunk_size, 5))
        diff_mtx[:, diff_code] = 1

        step_pos_labels = self.step_pos_labels[diff_idx, window_slice]
        step_type_labels = self.step_type_labels[diff_idx, window_slice, :]

        if self.conv:
            res = {
                'fft_features': torch.squeeze(fft_features.float(), 0),
                'diff': diff_mtx.astype(np.float32).reshape(-1),
                'step_pos_labels': step_pos_labels.astype(np.float32)
            }

        elif self.step_predictions is not None:
            step_predictions= \
                    self.step_predictions[diff_idx, window_slice]\
                        .reshape((-1, 1))

            res = {
                'fft_features': fft_features.float(),
                'diff': diff_mtx.astype(np.float32),
                'step_pos_labels': step_pos_labels.astype(np.float32),
                'step_type_labels': step_type_labels.astype(np.float32),
                'step_predictions': step_predictions.astype(np.float32)
            }
        else:
            res = {
                'fft_features': fft_features.float(),
                'diff': diff_mtx.astype(np.float32),
                'step_pos_labels': step_pos_labels.astype(np.float32),
                'step_type_labels': step_type_labels.astype(np.float32)
            }

        return res


    def save(self, dataset_name, base_path='datasets', fname=None):
        if fname is None:
            song_name = self.song_name
            fname = '%s/%s/%s/%s.h5' % \
                    (base_path, dataset_name, song_name, song_name)

        if not os.path.isdir('/'.join([base_path,dataset_name])):
            os.mkdir('/'.join([base_path,dataset_name]))

        if not os.path.isdir('/'.join([base_path,dataset_name,song_name])):
            os.mkdir('/'.join([base_path,dataset_name,song_name]))

        with h5py.File(fname, 'w') as hf:

            hf.attrs['song_name'] = self.song_name
            hf.attrs['diffs'] = np.array(self.diffs, dtype='S10')
            hf.attrs['context_size'] = self.context_size

            hf.create_dataset('fft_features', data=self.fft_features)
            hf.create_dataset('step_pos_labels', data=self.step_pos_labels)
            hf.create_dataset('step_type_labels', data=self.step_type_labels)

            if self.step_predictions is not None:
                hf.create_dataset('step_predictions',
                        data=self.step_predictions)


def load(
        fname,
        dataset_name,
        chunk_size=200,
        base_path=utils.BASE_PATH,
        context_size=None):

    h5name = f'{base_path}/datasets/{dataset_name}/{fname}/{fname}.h5'
    with h5py.File(h5name, 'r') as hf:

        song_name = hf.attrs['song_name']
        diffs = list(map(lambda x: x.decode('ascii'), hf.attrs['diffs']))

        if context_size is None:
            context_size = hf.attrs['context_size']

        fft_features = hf['fft_features'].value
        step_pos_labels = hf['step_pos_labels'].value
        step_type_labels = hf['step_type_labels'].value

        if 'step_predictions' in hf:
            step_predictions = hf['step_predictions'].value
        else:
            step_predictions = None

        return SMDataset(song_name, fft_features, step_pos_labels,
                step_type_labels, diffs, chunk_size, context_size,
                step_predictions)

