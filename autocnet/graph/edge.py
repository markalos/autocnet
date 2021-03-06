from functools import wraps
import warnings
from collections import MutableMapping

import numpy as np
import pandas as pd

from scipy.spatial.distance import cdist

import autocnet
from autocnet.graph.node import Node
from autocnet.utils import utils
from autocnet.matcher import cpu_outlier_detector as od
from autocnet.matcher import suppression_funcs as spf
from autocnet.matcher import subpixel as sp
from autocnet.transformation import fundamental_matrix as fm
from autocnet.transformation import homography as hm
from autocnet.vis.graph_view import plot_edge, plot_node, plot_edge_decomposition
from autocnet.cg import cg



class Edge(dict, MutableMapping):
    """
    Attributes
    ----------
    source : hashable
             The source node

    destination : hashable
                  The destination node
    masks : set
            A list of the available masking arrays

    weights : dict
             Dictionary with two keys overlap_area, and overlap_percn
             overlap_area returns the area overlaped by both images
             overlap_percn retuns the total percentage of overlap
    """

    def __init__(self, source=None, destination=None):
        self.source = source
        self.destination = destination
        self['homography'] = None
        self['fundamental_matrix'] = None
        self.matches = pd.DataFrame()
        self.masks = pd.DataFrame()
        self['weights'] = {}
        self['source_mbr'] = None
        self['destin_mbr'] = None

    def __repr__(self):
        return """
        Source Image Index: {}
        Destination Image Index: {}
        Available Masks: {}
        """.format(self.source, self.destination, self.masks)

    def __eq__(self, other):
        eq = True
        d = self.__dict__
        o = other.__dict__
        for k, v in d.items():
            # If the attribute key is missing they can not be equal
            if not k in o.keys():
                eq = False
                return eq

            if isinstance(v, pd.DataFrame):
                if not v.equals(o[k]):
                    eq = False
            elif isinstance(v, np.ndarray):
                if not v.all() == o[k].all():
                    eq = False

        return eq

    """@property
    def masks(self):
        mask_lookup = {'fundamental': 'fundamental_matrix'}
        if not hasattr(self, '_masks'):
            if isinstance(self.matches, pd.DataFrame):
                self._masks = pd.DataFrame(True, columns=['symmetry'],
                                           index=self.matches.index)
            else:
                self._masks = pd.DataFrame()
        return self._masks

    @masks.setter
    def masks(self, v):
        column_name = v[0]
        boolean_mask = v[1]
        self.masks[column_name] = boolean_mask"""

    def match(self, k=2, **kwargs):

        """
        Given two sets of descriptors, utilize a FLANN (Approximate Nearest
        Neighbor KDTree) matcher to find the k nearest matches.  Nearness is
        the euclidean distance between descriptors.

        The matches are then added as an attribute to the edge object.

        Parameters
        ----------
        k : int
            The number of neighbors to find

        overlap : boolean
                  Apply the matcher only to the overlapping area defined by
                  the source_mbr and destin_mbr attributes (stored in the
                  edge dict).
        """
        pass    

    def match_overlap(self, k=2, **kwargs):
        """
        Given two sets of descriptors, apply the matcher with the
        source and destination overlaps.
        """
        overlaps = [self['source_mbr'], self['destin_mbr']]
        self.match(k=k, overlap=overlaps, **kwargs)
        
    def decompose(self):
        """
        Apply coupled decomposition to the images and
        match identified sub-images
        """
        pass

    def decompose_and_match(*args, **kwargs):
        pass

    """
    def extract_subset(self, *args, **kwargs):
        self.compute_overlap()

        # Extract the source
        minx, maxx, miny, maxy = self['source_mbr']
        xystart = (minx, miny)
        pixels=[minx, miny, maxx-minx, maxy-miny]
        node = self.source
        arr = node.geodata.read_array(pixels=pixels)
        node.extract_features(arr, xystart=xystart, *args, **kwargs)

        # Extract the destination
        minx, maxx, miny, maxy = self['destin_mbr']
        xystart = (minx, miny)
        pixels=[minx, miny, maxx-minx, maxy-miny]
        node = self.destination
        arr = node.geodata.read_array(pixels=pixels)
        node.extract_features(arr, xystart=xystart, *args, **kwargs)
    """
    def symmetry_check(self):
        self.masks['symmetry'] = od.mirroring_test(self.matches)

    def ratio_check(self, clean_keys=[], maskname='ratio', **kwargs):
        matches, mask = self.clean(clean_keys)
        self.masks[maskname] = od.distance_ratio(matches, **kwargs)

    def compute_fundamental_matrix(self, clean_keys=[], maskname='fundamental', **kwargs):
        """
        Estimate the fundamental matrix (F) using the correspondences tagged to this
        edge.


        Parameters
        ----------
        clean_keys : list
                     Of strings used to apply masks to omit correspondences

        method : {linear, nonlinear}
                 Method to use to compute F.  Linear is significantly faster at
                 the cost of reduced accuracy.

        See Also
        --------
        autocnet.transformation.transformations.FundamentalMatrix

        """
        matches, mask = self.clean(clean_keys)

        # TODO: Homogeneous is horribly inefficient here, use Numpy array notation
        s_keypoints = self.get_keypoints('source', index=matches['source_idx'])
        d_keypoints = self.get_keypoints('destination', index=matches['destination_idx'])


        # Replace the index with the matches index.
        s_keypoints.index = matches.index
        d_keypoints.index = matches.index

        self['fundamental_matrix'], fmask = fm.compute_fundamental_matrix(s_keypoints, d_keypoints, **kwargs)

        if isinstance(self['fundamental_matrix'], np.ndarray):
            # Convert the truncated RANSAC mask back into a full length mask
            mask[mask] = fmask

            # Set the initial state of the fundamental mask in the masks
            self.masks[maskname] = mask

    def get_keypoints(self, node, index=None, homogeneous=True):
        node = getattr(self, node)
        return node.get_keypoint_coordinates(index=index, homogeneous=homogeneous)

    def compute_fundamental_error(self, clean_keys=[]):
        """
        Given a fundamental matrix, compute the reprojective error between
        a two sets of keypoints.

        Parameters
        ----------
        clean_keys : list
                     of string keys to masking arrays
                     (created by calling outlier detection)

        Returns
        -------
        error : pd.Series
                of reprojective error indexed to the matches data frame
        """
        if self['fundamental_matrix'] is None:
            warning.warn('No fundamental matrix has been compute for this edge.'
            )
        matches, masks = self.clean(clean_keys)

        source_kps = self.source.get_keypoint_coordinates(index=matches['source_idx'])
        destination_kps = self.destination.get_keypoint_coordinates(index=matches['destination_idx'])

        error = fm.compute_fundamental_error(self['fundamental_matrix'], source_kps, destination_kps)

        error = pd.Series(error, index=matches.index)
        return error

    def compute_homography(self, method='ransac', clean_keys=[], pid=None, maskname='homography', **kwargs):
        """
        For each edge in the (sub) graph, compute the homography
        Parameters
        ----------
        outlier_algorithm : object
                            An openCV outlier detections algorithm, e.g. cv2.RANSAC

        clean_keys : list
                     of string keys to masking arrays
                     (created by calling outlier detection)
        Returns
        -------
        transformation_matrix : ndarray
                                The 3x3 transformation matrix

        mask : ndarray
               Boolean array of the outliers
        """
        matches, mask = self.clean(clean_keys)

        s_keypoints = self.source.get_keypoint_coordinates(index=matches['source_idx'])
        d_keypoints = self.destination.get_keypoint_coordinates(index=matches['destination_idx'])

        self['homography'], hmask = hm.compute_homography(s_keypoints.values, d_keypoints.values)

        # Convert the truncated RANSAC mask back into a full length mask
        mask[mask] = hmask
        self.masks['homography'] = mask

    def subpixel_register(self, clean_keys=[], threshold=0.8,
                          template_size=19, search_size=53, max_x_shift=1.0,
                          max_y_shift=1.0, tiled=False, **kwargs):
        """
        For the entire graph, compute the subpixel offsets using pattern-matching and add the result
        as an attribute to each edge of the graph.

        Parameters
        ----------
        clean_keys : list
                     of string keys to masking arrays
                     (created by calling outlier detection)

        threshold : float
                    On the range [-1, 1].  Values less than or equal to
                    this threshold are masked and can be considered
                    outliers

        upsampling : int
                     The multiplier to the template and search shapes to upsample
                     for subpixel accuracy

        template_size : int
                        The size of the template in pixels, must be odd

        search_size : int
                      The size of the search

        max_x_shift : float
                      The maximum (positive) value that a pixel can shift in the x direction
                      without being considered an outlier

        max_y_shift : float
                      The maximum (positive) value that a pixel can shift in the y direction
                      without being considered an outlier
        """
        matches = self.matches
        for column, default in {'x_offset': 0, 'y_offset': 0, 'correlation': 0, 'reference': -1}.items():
            if column not in self.matches.columns:
                self.matches[column] = default

        # Build up a composite mask from all of the user specified masks
        matches, mask = self.clean(clean_keys)

        # Grab the full images, or handles
        if tiled is True:
            s_img = self.source.geodata
            d_img = self.destination.geodata
        else:
            s_img = self.source.geodata.read_array()
            d_img = self.destination.geodata.read_array()

        source_image = (matches.iloc[0]['source_image'])

        # for each edge, calculate this for each keypoint pair
        for i, (idx, row) in enumerate(matches.iterrows()):
            s_idx = int(row['source_idx'])
            d_idx = int(row['destination_idx'])

            s_keypoint = self.source.get_keypoint_coordinates(s_idx)
            d_keypoint = self.destination.get_keypoint_coordinates(d_idx)

            # Get the template and search window
            s_template = sp.clip_roi(s_img, s_keypoint, template_size)
            d_search = sp.clip_roi(d_img, d_keypoint, search_size)
            try:
                x_offset, y_offset, strength = sp.subpixel_offset(s_template, d_search, **kwargs)
                self.matches.loc[idx, ('x_offset', 'y_offset',
                                       'correlation', 'reference')] = [x_offset, y_offset, strength, source_image]
            except:
                warnings.warn('Template-Search size mismatch, failing for this correspondence point.')

        # Compute the mask for correlations less than the threshold
        threshold_mask = self.matches['correlation'] >= threshold

        # Compute the mask for the point shifts that are too large
        query_string = 'x_offset <= -{0} or x_offset >= {0} or y_offset <= -{1} or y_offset >= {1}'.format(max_x_shift,max_y_shift)
        sp_shift_outliers = self.matches.query(query_string)
        shift_mask = pd.Series(True, index=self.matches.index)
        shift_mask.loc[sp_shift_outliers.index] = False

        # Generate the composite mask and write the masks to the mask data structure
        mask = threshold_mask & shift_mask
        self.masks['shift'] = shift_mask
        self.masks['threshold'] = threshold_mask
        self.masks['subpixel'] = mask

    def suppress(self, suppression_func=spf.correlation, clean_keys=[], maskname='suppression', **kwargs):
        """
        Apply a disc based suppression algorithm to get a good spatial
        distribution of high quality points, where the user defines some
        function to be used as the quality metric.

        Parameters
        ----------
        suppression_func : object
                           A function that returns a scalar value to be used
                           as the strength of a given row in the matches data
                           frame.

        suppression_args : tuple
                           Arguments to be passed on to the suppression function

        clean_keys : list
                     of mask keys to be used to reduce the total size
                     of the matches dataframe.
        """
        if not isinstance(self.matches, pd.DataFrame):
            raise AttributeError('This edge does not yet have any matches computed.')

        matches, mask = self.clean(clean_keys)
        domain = self.source.geodata.raster_size

        # Massage the dataframe into the correct structure
        coords = self.source.get_keypoint_coordinates()
        merged = matches.merge(coords, left_on=['source_idx'], right_index=True)
        merged['strength'] = merged.apply(suppression_func, axis=1, args=([self]))

        smask, k = od.spatial_suppression(merged, domain, **kwargs)

        mask[mask] = smask
        self.masks[maskname] = mask

    def plot_source(self, ax=None, clean_keys=[], **kwargs):  # pragma: no cover
        matches, mask = self.clean(clean_keys=clean_keys)
        indices = pd.Index(matches['source_idx'].values)
        return plot_node(self.source, index_mask=indices, **kwargs)

    def plot_destination(self, ax=None, clean_keys=[], **kwargs):  # pragma: no cover
        matches, mask = self.clean(clean_keys=clean_keys)
        indices = pd.Index(matches['destination_idx'].values)
        return plot_node(self.destination, index_mask=indices, **kwargs)

    def plot(self, ax=None, clean_keys=[], node=None, **kwargs):  # pragma: no cover
        dest_keys = [0, '0', 'destination', 'd', 'dest']
        source_keys = [1, '1', 'source', 's']

        # If node is not none, plot a single node
        if node in source_keys:
            return self.plot_source(self, clean_keys=clean_keys, **kwargs)

        elif node in dest_keys:
            return self.plot_destination(self, clean_keys=clean_keys, **kwargs)

        # Else, plot the whole edge
        return plot_edge(self, ax=ax, clean_keys=clean_keys, **kwargs)

    def plot_decomposition(self, *args, **kwargs): #pragma: no cover
        return plot_edge_decomposition(self, *args, **kwargs)

    def clean(self, clean_keys):
        """
        Given a list of clean keys compute the mask of valid
        matches

        Parameters
        ----------
        clean_keys : list
                     of columns names (clean keys)

        Returns
        -------
        matches : dataframe
                  A masked view of the matches dataframe

        mask : series
               A boolean series to inflate back to the full match set
        """
        if clean_keys:
            mask = self.masks[clean_keys].all(axis=1)
        else:
            mask = pd.Series(True, self.matches.index)

        return self.matches[mask], mask

    def overlap(self):
        """
        Acts on an edge and returns the overlap area and percentage of overlap
        between the two images on the edge. Data is returned to the
        weights dictionary
        """
        poly1 = self.source.geodata.footprint
        poly2 = self.destination.geodata.footprint

        overlapinfo = cg.two_poly_overlap(poly1, poly2)

        self['weights']['overlap_area'] = overlapinfo[1]
        self['weights']['overlap_percn'] = overlapinfo[0]

    def coverage(self, clean_keys = []):
        """
        Acts on the edge given either the source node
        or the destination node and returns the percentage
        of overlap covered by the keypoints. Data for the
        overlap is gathered from the source node of the edge
        resulting in a maximum area difference of 2% when compared
        to the destination.

        Returns
        -------
        total_overlap_percentage : float
                                   returns the overlap area
                                   covered by the keypoints
        """
        matches, mask = self.clean(clean_keys)
        source_array = self.source.get_keypoint_coordinates(index=matches['source_idx']).values

        source_coords = self.source.geodata.latlon_corners
        destination_coords = self.destination.geodata.latlon_corners

        convex_hull = cg.convex_hull(source_array)

        convex_points = [self.source.geodata.pixel_to_latlon(row[0], row[1]) for row in convex_hull.points[convex_hull.vertices]]
        convex_coords = [(x, y) for x, y in convex_points]

        source_poly = utils.array_to_poly(source_coords)
        destination_poly = utils.array_to_poly(destination_coords)
        convex_poly = utils.array_to_poly(convex_coords)

        intersection_area = cg.get_area(source_poly, destination_poly)

        total_overlap_coverage = (convex_poly.GetArea()/intersection_area)

        return total_overlap_coverage

    def compute_weights(self, clean_keys, **kwargs):
        """
        Computes a voronoi diagram for the overlap between two images
        then gets the area of each polygon resulting in a voronoi weight.
        These weights are then appended to the matches dataframe.

        Parameters
        ----------
        clean_keys : list
                     Of strings used to apply masks to omit correspondences

        """
        if not isinstance(self.matches, pd.DataFrame):
            raise AttributeError('Matches have not been computed for this edge')
        voronoi = cg.vor(self, clean_keys, **kwargs)
        self.matches = pd.concat([self.matches, voronoi[1]['vor_weights']], axis=1)



    def compute_overlap(self, **kwargs):
        """
        Estimate a source and destination minimum bounding rectangle, in
        pixel space
        """
        self.overlap_latlon_coords, self["source_mbr"], self["destin_mbr"] = self.source.geodata.compute_overlap(self.destination.geodata, **kwargs)
