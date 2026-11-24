"""Authored facial controls, source skinning and safe expression fallbacks."""
import unittest
from unittest import mock

import numpy as np

from pmx_export import build_model
from pmx_morphs import bake_offsets, local_deltas, world_deltas
from tests.test_pmx_rig import humanoid


class AuthoredMorphTests(unittest.TestCase):
    def test_morphs_skin_each_source_joint_before_blending(self):
        # A shared lip vertex has two different reference joint transforms.
        # Transforming its already blended position produces a different shape.
        reference = np.repeat(np.eye(4)[None], 2, axis=0)
        reference[0, 0, 3] = 1; reference[1, 1, 3] = 2
        delta = np.repeat(np.eye(4)[None], 2, axis=0)
        delta[0, :2, :2] = ((0, -1), (1, 0))
        actual = bake_offsets(np.array(((2., 0., 0., 1.),)), np.array(((0, 1),)),
                              np.array(((.5, .5),)), reference, delta)
        np.testing.assert_allclose(actual, ((-1.5, 1.5, 0.),), atol=1e-12)

    def test_eighth_face_influence_survives_morph_baking(self):
        reference = np.repeat(np.eye(4)[None], 8, axis=0); delta = reference.copy()
        delta[7, 1, 3] = 8
        result = bake_offsets(np.array(((1., 2., 3., 1.),)), np.arange(8)[None],
                              np.full((1, 8), 1/8), reference, delta)
        np.testing.assert_allclose(result, ((0., 1., 0.),))

    def test_identity_targets_preserve_customized_vertices_exactly(self):
        reference = np.repeat(np.eye(4)[None], 2, axis=0)
        reference[0, :3, :3] *= (1.1, .9, 1.2); reference[1, :3, 3] = (3, 4, 5)
        points = np.array(((1., 2., 3., 1.), (4., 5., 6., 1.)))
        before = points.copy()
        result = bake_offsets(points, np.array(((0, 1), (1, 0))), np.array(((.3, .7), (.6, .4))),
                              reference, np.repeat(np.eye(4)[None], 2, axis=0))
        np.testing.assert_array_equal(result, np.zeros((2, 3)))
        np.testing.assert_array_equal(points, before)

    def test_face_regions_and_one_sided_winks_do_not_share_controls(self):
        names = ['j_f_eye_l', 'j_f_eye_r', 'j_f_mabup_01_l', 'j_f_uslip_l', 'j_f_mayu_l', 'j_kao']
        neutral = {i: np.eye(4) for i in range(len(names))}
        posed = {i: np.eye(4) for i in neutral}
        for matrix in posed.values(): matrix[1, 3] = .1
        self.assertEqual(set(local_deltas(neutral, posed, names, 'eyes', 'l')), {0, 2})
        self.assertEqual(set(local_deltas(neutral, posed, names, 'mouth')), {3})
        self.assertEqual(set(local_deltas(neutral, posed, names, 'brows')), {4})

    def test_local_axes_and_unanimated_customization_are_retained(self):
        reference = np.repeat(np.eye(4)[None], 3, axis=0)
        reference[0, :2, :2] = ((0, -1), (1, 0))
        reference[0, :3, 3] = (2, 3, 4)
        reference[1] = reference[0]; reference[2] = reference[0]
        correction = np.eye(4); correction[0, 3] = .2
        result = world_deltas(reference, [-1, 0, 0], {1: correction})
        # Local +X under this head frame is global +Y.
        np.testing.assert_allclose(result[1, :3, 3], (0., .2, 0.), atol=1e-12)
        np.testing.assert_allclose(result[[0, 2]], np.repeat(np.eye(4)[None], 2, axis=0), atol=1e-12)

    def test_unsupported_native_face_is_reported_without_fixed_angle_fallback(self):
        scene, outfit = humanoid()
        with mock.patch('pmx_morphs.authored_targets', return_value=([], ['原始表情未支持'])):
            model = build_model(scene, outfit, rig='standard')
        self.assertEqual(model.expression_source, 'native_facial')
        self.assertEqual(model.morphs, [])
        self.assertIn('原始表情未支持', model.warnings)

    def test_disabled_expressions_do_not_read_facial_resources(self):
        scene, outfit = humanoid()
        with mock.patch('pmx_morphs.authored_targets') as loader:
            model = build_model(scene, outfit, rig='standard', expressions=False)
        loader.assert_not_called()
        self.assertEqual(model.morphs, [])
        self.assertEqual(model.expression_source, 'none')


if __name__ == '__main__': unittest.main()
