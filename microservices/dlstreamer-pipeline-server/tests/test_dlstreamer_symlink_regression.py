#
# Apache v2 license
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for the symlink-handling contradiction between this service's
path validators (src/server/path_validator.py, dlsps2/src/config/path_validator.py)
and the dlstreamer GStreamer element they feed.

Context: dlstreamer's `nsz-security-fixes-2026-3` branch adds a hard rejection of
ANY symlinked `file-path` in the `gvametapublish` element (method=file), regardless
of where the symlink resolves to. This service's own path validators explicitly
ALLOW a symlink as long as it *resolves* inside the configured resources
directory (see the docstrings in both path_validator.py modules). That means a
request which passes this service's own validation can still fail at the
GStreamer element level once the service picks up a dlstreamer release that
includes the symlink-rejection fix.

These tests pin down TODAY's validator behavior (permissive) so that whoever
bumps the pinned dlstreamer/dlstreamer-pipeline-server base image version gets a
clear, actionable test failure here instead of a silent runtime regression the
first time a customer configures a symlinked destination file path.
"""

import os

import pytest


class TestLegacyPathValidatorAllowsSymlinks:
    """src/server/path_validator.py (legacy named-pipeline API)."""

    def test_symlink_resolving_inside_resources_is_allowed_today(self, tmp_path, monkeypatch):
        import src.server.path_validator as path_validator

        monkeypatch.setattr(path_validator, "RESOURCES_BASE_DIR", tmp_path)

        real_file = tmp_path / "results_real.jsonl"
        real_file.write_text("{}")
        symlink_path = tmp_path / "results.jsonl"
        symlink_path.symlink_to(real_file)

        # Documents TODAY's permissive behavior. If this starts failing, it means
        # validate_file_path() was tightened to reject symlinks too - update this
        # test and re-check whether the gvametapublish contradiction below is gone.
        assert path_validator.validate_file_path(str(symlink_path)) is True

    def test_symlink_escaping_resources_is_still_rejected(self, tmp_path, monkeypatch):
        import src.server.path_validator as path_validator

        resources_dir = tmp_path / "resources"
        resources_dir.mkdir()
        monkeypatch.setattr(path_validator, "RESOURCES_BASE_DIR", resources_dir)

        outside_file = tmp_path / "outside.jsonl"
        outside_file.write_text("{}")
        symlink_path = resources_dir / "results.jsonl"
        symlink_path.symlink_to(outside_file)

        assert path_validator.validate_file_path(str(symlink_path)) is False


class TestDlsps2PathValidatorAllowsSymlinks:
    """dlsps2/src/config/path_validator.py (new config API) - same contract."""

    def test_symlink_resolving_inside_resources_is_allowed_today(self, tmp_path, monkeypatch):
        import sys
        dlsps2_src = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "dlsps2", "src",
        )
        sys.path.insert(0, os.path.abspath(dlsps2_src))
        from config import path_validator as dlsps2_path_validator

        monkeypatch.setattr(dlsps2_path_validator, "RESOURCES_BASE_DIR", tmp_path)

        real_file = tmp_path / "results_real.jsonl"
        real_file.write_text("{}")
        symlink_path = tmp_path / "results.jsonl"
        symlink_path.symlink_to(real_file)

        assert dlsps2_path_validator.validate_file_path(str(symlink_path)) is True


class TestGvaMetaPublishSymlinkRejection:
    """Real (non-mocked) check against the actual gvametapublish GStreamer element,
    when it's installed in the test environment (the service's own Docker test
    image, built from a real dlstreamer base image, has it - a bare dev box
    running these tests outside Docker typically will not).
    """

    @staticmethod
    def _gst_or_skip():
        gi = pytest.importorskip("gi", reason="Requires PyGObject")
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        if Gst.ElementFactory.find("gvametapublish") is None:
            pytest.skip("gvametapublish plugin not installed in this environment "
                        "(expected outside the service's dlstreamer-based Docker test image)")
        return Gst

    def test_symlinked_file_path_pipeline_state_change(self, tmp_path):
        """Canary: if/when the pinned dlstreamer version includes the
        nsz-security-fixes-2026-3 symlink-rejection fix, this element will refuse
        to start for a symlinked file-path and this test will start FAILING -
        signaling that path_validator.py's "symlinks are fine if they resolve
        inside resources/" contract no longer matches dlstreamer's actual
        behavior, and needs to be revisited together.
        """
        Gst = self._gst_or_skip()

        real_file = tmp_path / "results_real.jsonl"
        real_file.write_text("")
        symlink_path = tmp_path / "results.jsonl"
        symlink_path.symlink_to(real_file)

        element = Gst.ElementFactory.make("gvametapublish", "destination")
        assert element is not None
        element.set_property("method", "file")
        element.set_property("file-path", str(symlink_path))

        ret, _state, _pending = element.set_state(Gst.State.PAUSED)
        element.set_state(Gst.State.NULL)

        assert ret != Gst.StateChangeReturn.FAILURE, (
            "gvametapublish refused to start for a symlinked file-path that "
            "path_validator.py's validate_file_path() currently allows (resolves "
            "inside resources/). The dlstreamer dependency pinned by this service "
            "now rejects ALL symlinked destinations unconditionally - update "
            "path_validator.py to reject symlinks too (or document/accept the "
            "new failure mode) before shipping this dlstreamer version."
        )
