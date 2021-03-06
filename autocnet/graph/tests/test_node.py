import os
import sys

import unittest
from unittest.mock import Mock, MagicMock
import warnings

import numpy as np
import pandas as pd
import pytest

from autocnet.examples import get_path
from plio.io.io_gdal import GeoDataset

from .. import node

sys.path.insert(0, os.path.abspath('..'))


class TestNode(object):

    @pytest.fixture
    def node(self):
        img = get_path('AS15-M-0295_SML.png')
        return node.Node(image_name='AS15-M-0295_SML',
                              image_path=img)

    def test_get_handle(self, node):
        assert isinstance(node.geodata, GeoDataset)

    def test_get_byte_array(self, node):
        image = node.get_byte_array()
        assert (1012, 1012) == image.shape
        assert np.uint8 == image.dtype

    def test_get_array(self, node):
        image = node.get_array()
        assert (1012, 1012) == image.shape
        assert np.float32 == image.dtype

    def test_extract_features(self, node):
        image = node.get_array()
        node.extract_features(image, extractor_parameters={'nfeatures': 10})
        assert len(node.get_keypoints()) ==  10
        assert len(node.descriptors) == 10
        assert 10 == node.nkeypoints

    def test_extract_downsampled_features(self, node):
        # Trust that the
        img = np.random.random(size=(1000,1000))
        geodata = Mock(spec=GeoDataset)
        geodata.raster_size = img.shape
        geodata.read_array = MagicMock(return_value=img)
        node.extract_features_with_downsampling(5,
                                                extractor_parameters={'nfeatures':10})

        assert len(node.keypoints) in range(8,12)
        assert node.keypoints['x'].max() > 500


    def test_extract_tiled_features(self, node):
        tilesize = 500
        node.extract_features_with_tiling(tilesize=tilesize, overlap=50,
                                          extractor_parameters={'nfeatures':10})

        kps = node.keypoints
        assert kps['x'].min() < tilesize
        assert kps['y'].min() < tilesize
        assert len(kps) == pytest.approx(90, 3)

    def test_masks(self, node):
        image = node.get_array()
        node.extract_features(image, extractor_parameters={'nfeatures': 5})
        assert isinstance(node.masks, pd.DataFrame)
        # Create an artificial mask
        node.masks['foo'] =  np.array([0, 0, 1, 1, 1], dtype=np.bool)
        assert node.masks['foo'].sum() == 3

    def test_convex_hull_ratio_fail(self):
        # Convex hull computation is checked lower in the hull computation
        #self.assertRaises(AttributeError, node.coverage_ratio)
        pass

    def test_isis_serial(self, node):
        serial = node.isis_serial
        assert None == serial

    def test_save_load(self, node, tmpdir):
        # Test that without keypoints this warns
        with pytest.warns(UserWarning) as warn:
            node.save_features(tmpdir.join('noattr.npy'))
        assert len(warn) == 1

        basename = tmpdir.dirname

        # With keypoints to npy
        reference = pd.DataFrame(np.arange(10).reshape(5,2), columns=['x', 'y'])
        node.keypoints = reference
        tmpdir.join('kps.npz')
        node.save_features(os.path.join(basename, 'kps.npz'))
        node.keypoints = None
        node.load_features(os.path.join(basename, 'kps.npz'))
        assert node.keypoints.equals(reference)

    def test_coverage(self, node):
        image = node.get_array()
        node.extract_features(image, extractor_method='sift', extractor_parameters={'nfeatures': 10})
        coverage_percn = node.coverage()
        assert coverage_percn == pytest.approx(38.06139557, 2)

    def test_clean(self, node):
        with pytest.raises(AttributeError):
            node._clean([])
        node.keypoints = pd.DataFrame(np.arange(5))
        node.masks = pd.DataFrame(np.array([[True, True, True, False, False],
                                   [True, False, True, True, False]]).T,
                                   columns=['a', 'b'])
        matches, mask = node._clean(clean_keys=['a'])
        assert mask.equals(pd.Series([True, True, True, False, False]))
