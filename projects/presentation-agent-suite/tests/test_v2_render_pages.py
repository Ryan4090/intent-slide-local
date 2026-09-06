"""Real G4 PNG/path/hash checks with only external rendering mocked."""
import unittest
from PIL import Image

import test_v2_verification as fixtures
from presentation_agents.v2.contracts import ContractError, file_hash


class RenderPageReceiptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerificationTests()
        self.fixture.setUp()
        self.directory = self.fixture.project / '_pptx_render/candidate-grid-pages'

    def tearDown(self):
        self.fixture.tearDown()

    def test_page_artifacts_bind_actual_png_bytes_uid_order_and_dimensions(self):
        receipt = self.fixture._isolated_fallback()
        self.assertEqual(len(receipt['render_pages']), 1)
        page = receipt['render_pages'][0]
        self.assertEqual((page['slide_uid'], page['page'], page['width'], page['height']), ('slide-1', 1, 1920, 1080))
        artifact = next(a for a in receipt['artifacts'] if a['kind'] == 'render_page')
        self.assertEqual(artifact['path'], page['path'])
        self.assertEqual(artifact['sha256'], file_hash(self.fixture.workspace / page['path']))

    def test_cross_directory_traversal_absolute_and_wrong_order_paths_are_rejected(self):
        for path in ('../P01.png', '/P01.png', 'foreign/P01.png', 'candidate-grid-pages/P02.png'):
            with self.subTest(path=path), self.assertRaises(ContractError):
                self.fixture._isolated_fallback(lambda proof: proof['pages'][0].update(image_path=path))

    def test_declared_hash_and_dimensions_must_match_the_read_bytes(self):
        for key, value in (('image_sha256', '0' * 64), ('image_width', 1008), ('image_height', True)):
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.fixture._isolated_fallback(lambda proof: proof['pages'][0].update({key: value}))

    def test_missing_extra_and_symlink_images_cannot_enter_the_receipt(self):
        def mutate(proof, case):
            image = self.directory / 'P01.png'
            if case == 'missing':
                image.unlink()
            elif case == 'extra':
                (self.directory / 'P02.png').write_bytes(image.read_bytes())
            else:
                outside = self.fixture.workspace / 'outside.png'
                outside.write_bytes(image.read_bytes())
                image.unlink()
                image.symlink_to(outside)
        for case in ('missing', 'extra', 'symlink'):
            with self.subTest(case=case), self.assertRaises(ContractError):
                self.fixture._isolated_fallback(lambda proof: mutate(proof, case))

    def test_failed_image_validation_discards_partial_render_outputs(self):
        with self.assertRaises(ContractError):
            self.fixture._isolated_fallback(lambda proof: proof['pages'][0].update(image_sha256='0' * 64))
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.directory.with_name('candidate-grid.png').exists())
        self.assertFalse(self.directory.with_name('candidate-grid.render.json').exists())

    def test_low_resolution_and_invalid_image_bytes_are_rejected_even_with_matching_hash(self):
        def mutate(proof, invalid):
            image = self.directory / 'P01.png'
            if invalid:
                image.write_bytes(b'not a PNG')
            else:
                Image.new('RGB', (960, 540), 'white').save(image)
                proof['pages'][0].update(image_width=960, image_height=540)
            proof['pages'][0]['image_sha256'] = file_hash(image)
        for invalid in (False, True):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                self.fixture._isolated_fallback(lambda proof: mutate(proof, invalid))

    def test_cancellation_after_render_discards_pages_grid_and_proof(self):
        state = {'cancelled': False}
        with self.assertRaisesRegex(ContractError, 'cancelled'):
            self.fixture._isolated_fallback(lambda proof: state.update(cancelled=True),
                                            cancelled=lambda: state['cancelled'])
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.directory.with_name('candidate-grid.png').exists())
        self.assertFalse(self.directory.with_name('candidate-grid.render.json').exists())


if __name__ == '__main__':
    unittest.main()
