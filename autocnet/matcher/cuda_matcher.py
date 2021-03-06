import warnings

import cudasift as cs
import numpy as np
import pandas as pd

def match(self, ratio=0.8, overlap=[], **kwargs):

    """
    Apply a composite CUDA matcher and ratio check.  If this method is used,
    no additional ratio check is necessary and no symmetry check is required.
    The ratio check is embedded on the cuda side and returned as an
    ambiguity value.  In testing symmetry is not required as it is expensive
    without significant gain in accuracy when using this implementation.
    """

    if overlap:
        source_overlap = overlap[0]
        source_kps = self.source.keypoints.query('x >= {} and x <= {} and y >= {} and y <= {}'.format(*source_overlap))
        idx = source_kps.index
        sremap = {k:v for k, v in enumerate(idx)}
        source_des = self.source.descriptors[idx]

        destin_overlap = overlap[1]
        destin_kps = self.destination.keypoints.query('x >= {} and x <= {} and y >= {} and y <= {}'.format(*destin_overlap))
        idx = destin_kps.index
        dremap = {k:v for k, v in enumerate(idx)}
        destin_des = self.destination.descriptors[idx]
    else:
        source_kps = self.source.get_keypoints()
        source_des = self.source.descriptors

        destin_kps = self.destination.get_keypoints()
        destin_des = self.destination.descriptors

    s_siftdata = cs.PySiftData.from_data_frame(source_kps, source_des)
    d_siftdata = cs.PySiftData.from_data_frame(destin_kps, destin_des)

    cs.PyMatchSiftData(s_siftdata, d_siftdata)
    matches, _ = s_siftdata.to_data_frame()
    source = np.empty(len(matches))
    source[:] = self.source['node_id']
    destination = np.empty(len(matches))
    destination[:] = self.destination['node_id']

    df = pd.concat([pd.Series(source), pd.Series(matches.index),
		    pd.Series(destination), matches.match,
		    matches.score, matches.ambiguity], axis=1)
    df.columns = ['source_image', 'source_idx', 'destination_image',
		    'destination_idx', 'score', 'ambiguity']


    if overlap:
        df['source_idx'].replace(sremap, inplace=True)
        df['destination_idx'].replace(dremap, inplace=True)

    # Set the matches and set the 'ratio' (ambiguity) mask
    self.matches = df
    self.masks['ratio'] =  df['ambiguity'] <= ratio
