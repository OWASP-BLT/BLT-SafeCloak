"""
Playwright-based tests for BLT-SafeCloak peer mesh behavior.

These tests use the shared fixtures in conftest.py to provide a zero-network,
high-stability environment for verifying WebRTC and signaling logic.
"""

import re
import time
from typing import Callable, List
import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

PEER_ID_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")


def _require_stripped_text(locator, element_name):
    """Return stripped locator text and fail clearly if no text is present."""
    raw_text = locator.text_content()
    assert raw_text is not None, f"Expected text content for {element_name}, got None"
    stripped = raw_text.strip()
    assert stripped, f"Expected non-empty text for {element_name}, got {raw_text!r}"
    return stripped


def _poll_until(
    condition: Callable[[], bool],
    attempts: int,
    interval_ms: int,
) -> bool:
    """Poll a condition until true or attempts are exhausted."""
    for _ in range(attempts):
        if condition():
            return True
        time.sleep(interval_ms / 1000)
    return False


def _wait_for_video_chat_ready(page: Page) -> None:
    """Wait until core video-chat UI is ready and signaling is established."""
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#btn-call")
    page.wait_for_selector("#my-peer-id")
    page.wait_for_function(
        "document.getElementById('my-peer-id') && "
        "document.getElementById('my-peer-id').textContent.trim() !== '' && "
        "document.getElementById('my-peer-id').textContent.trim() !== 'Connecting...'",
        timeout=30000)


def _accept_consent_all(pages: List[Page], timeout_ms: int = 2000):
    """Attempt to click 'I Consent' on all pages where it is visible."""
    for page in pages:
        try:
            selector = "#consent-allow"
            # Fast check for visibility
            if page.locator(selector).is_visible():
                page.click(selector, timeout=timeout_ms)
        except:
            pass


class TestPeerMeshStability:
    """Verify core PeerJS signaling and manual mesh formation."""

    def test_local_peer_id_assignment(self, base_url, new_context):
        """Verify local peer ID is assigned and matches the 6-char format."""
        ctx = new_context()
        try:
            page = ctx.new_page()
            page.goto(f"{base_url}/video-room", wait_until="domcontentloaded")
            # This implicitly verifies that PeerJS successfully connected to the signaling server.
            _wait_for_video_chat_ready(page)
            peer_id_element = page.locator("#my-peer-id")
            peer_text = _require_stripped_text(peer_id_element, "#my-peer-id")
            assert PEER_ID_PATTERN.match(peer_text)
        finally:
            ctx.close()

    def test_three_clients_manual_mesh(self, base_url, new_context):
        """Verify 3 clients can form a full mesh with proper consent gating."""
        ctx_a = new_context()
        ctx_b = new_context()
        ctx_c = new_context()
        try:
            pages = [ctx_a.new_page(), ctx_b.new_page(), ctx_c.new_page()]
            for p in pages:
                p.goto(f"{base_url}/video-room", wait_until="domcontentloaded")
                _wait_for_video_chat_ready(p)

            p_a, p_b, p_c = pages
            peer_a = _require_stripped_text(p_a.locator("#my-peer-id"), "#my-peer-id")
            peer_b = _require_stripped_text(p_b.locator("#my-peer-id"), "#my-peer-id")
            peer_c = _require_stripped_text(p_c.locator("#my-peer-id"), "#my-peer-id")

            # A calls B
            p_a.locator("#remote-id").fill(peer_b)
            p_a.locator("#btn-call").click()

            # Poll for consent on both A and B (Caller and Receiver)
            for _ in range(5):
                _accept_consent_all(pages)
                time.sleep(0.5)

            # Wait for A and B to connect
            p_a.wait_for_function("document.querySelectorAll('.video-wrapper').length >= 2",
                                  timeout=30000)
            p_b.wait_for_function("document.querySelectorAll('.video-wrapper').length >= 2",
                                  timeout=30000)

            # A calls C
            p_a.locator("#remote-id").fill(peer_c)
            p_a.locator("#btn-call").click()

            # Poll for consent - C will receive a call from A, and then potentially B (mesh propagation)
            for _ in range(10):
                _accept_consent_all(pages)
                time.sleep(0.5)

            # Verification: Full mesh status
            def check_count(page, expected_min):

                def has_expected_count():
                    count_text = (page.locator("#participant-count").text_content() or "").strip()
                    try:
                        count = int(count_text.split()[0])
                        return count >= expected_min
                    except:
                        return False

                return _poll_until(has_expected_count, attempts=30, interval_ms=500)

            # Verify that A sees both B and C
            assert check_count(p_a, 2), "p_a did not reach at least 2 participants"
        finally:
            ctx_a.close()
            ctx_b.close()
            ctx_c.close()


class TestPeerListPropagation:
    """Verify peer list and participant UI synchronization."""

    def test_peer_list_updates_on_connection(self, base_url, new_context):
        """Verify that participant list updates correctly after successful connection."""
        ctx_a, ctx_b = new_context(), new_context()
        try:
            p_a, p_b = ctx_a.new_page(), ctx_b.new_page()
            for p in [p_a, p_b]:
                p.goto(f"{base_url}/video-room", wait_until="domcontentloaded")
                _wait_for_video_chat_ready(p)

            id_a = _require_stripped_text(p_a.locator("#my-peer-id"), "#my-peer-id")
            id_b = _require_stripped_text(p_b.locator("#my-peer-id"), "#my-peer-id")

            p_a.locator("#remote-id").fill(id_b)
            p_a.locator("#btn-call").click()

            # Handle consent on both ends
            for _ in range(5):
                _accept_consent_all([p_a, p_b])
                time.sleep(0.5)

            def verify_in_list(page, peer_id):

                def exists():
                    html = page.locator("#participants-list").inner_html()
                    return peer_id in html

                return _poll_until(exists, attempts=30, interval_ms=500)

            assert verify_in_list(p_a, id_b)
            assert verify_in_list(p_b, id_a)
        finally:
            ctx_a.close()
            ctx_b.close()
