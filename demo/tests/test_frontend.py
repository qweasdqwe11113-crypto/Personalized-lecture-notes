"""Execute real frontend scripts with controlled async responses, using Node's built-in VM."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Frontend regressions require Node.js')
class FrontendTests(unittest.TestCase):
    def run_case(self, name):
        result = subprocess.run(['node', str(Path(__file__).with_name('frontend_regressions.cjs')), name],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_markdown_keeps_lists_on_the_correct_side_of_formulas(self):
        self.run_case('markdownOrder')

    def test_markdown_renders_formulas_tables_and_figures_without_misreading_money(self):
        self.run_case('richMarkdown')

    def test_submission_carries_writing_mode_and_format_with_safe_defaults(self):
        self.run_case('submissionCarriesPipelineChoices')

    def test_markdown_keeps_other_fence_markers_inside_formula_text(self):
        self.run_case('markdownFenceContent')

    def test_slow_image_response_cannot_replace_the_current_upload_preview(self):
        self.run_case('imagePreviewOrder')

    def test_accepted_job_is_watched_even_if_history_refresh_fails(self):
        self.run_case('acceptedJobSurvivesHistoryFailure')

    def test_slow_upload_response_cannot_replace_newer_document_text(self):
        self.run_case('uploadPreviewOrder')

    def test_automatic_length_stays_blank_when_restoring_saved_lectures(self):
        self.run_case('automaticLengthRemainsOptional')

    def test_background_completion_preserves_the_teachers_current_page(self):
        self.run_case('backgroundCompletionPreservesPage')

    def test_refreshing_images_preserves_live_choices_and_survives_failure(self):
        self.run_case('refreshingImagesKeepsLiveSelection')
