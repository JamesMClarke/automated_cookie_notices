# -*- coding: utf-8 -*-
"""
Tranco Screen Reader Crawler - NVDA Add-on

Captures NVDA speech output, coordinated with the Selenium crawler
via command/result JSON files in ~/Desktop/tranco_nvda_crawl/.

The Selenium crawler writes:
    nvda_command.json  {"action": "start"|"stop"|"navigate"}

This addon reads commands, captures speech, and writes:
    nvda_result.json   {"segments": [...], "navigation": {...}}

Commands:
    start     - Begin capturing speech (passive, for page-load announcements)
    stop      - Stop capturing, write speech segments to result file
    navigate  - Actively walk the virtual buffer (headings, links, landmarks,
                line-by-line), capture everything NVDA speaks, write results

Keyboard shortcut:
    NVDA+Shift+T: Toggle the file watcher on/off
"""

import os
import json
import time
import threading
from datetime import datetime

import globalPluginHandler
import speech
import api
import ui
import browseMode
import textInfos
import controlTypes
import treeInterceptorHandler
from scriptHandler import script
from logHandler import log
import queueHandler

# NVDA renamed this constant in 2021; support both versions.
try:
    _REASON_CARET = controlTypes.OutputReason.CARET
except AttributeError:
    _REASON_CARET = controlTypes.REASON_CARET


# Communication directory (must match crawler.py)
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
COMM_DIR = os.path.join(DESKTOP, "nvda_crawl")
COMMAND_FILE = os.path.join(COMM_DIR, "nvda_command.json")
RESULT_FILE = os.path.join(COMM_DIR, "nvda_result.json")

POLL_INTERVAL = 0.5  # seconds between file-watcher polls
NAV_DELAY = 0.05     # delay between navigation steps (reduced to keep total time < 60s)


class SpeechCapture(object):
    """Captures all speech output from NVDA."""

    def __init__(self):
        self.capturing = False
        self.segments = []
        self.start_time = None
        self._original_speak = None
        self._original_speakText = None

    def start(self):
        self.capturing = True
        self.segments = []
        self.start_time = datetime.now()

        if self._original_speak is None:
            self._original_speak = speech.speak
            speech.speak = self._hooked_speak

        if self._original_speakText is None:
            self._original_speakText = speech.speakText
            speech.speakText = self._hooked_speakText

    def stop(self):
        self.capturing = False

        if self._original_speak is not None:
            speech.speak = self._original_speak
            self._original_speak = None

        if self._original_speakText is not None:
            speech.speakText = self._original_speakText
            self._original_speakText = None

        return self.segments

    def _hooked_speak(self, speechSequence, *args, **kwargs):
        if self.capturing and speechSequence:
            text_parts = []
            for item in speechSequence:
                if isinstance(item, str):
                    text_parts.append(item)

            if text_parts:
                self.segments.append({
                    "text": " ".join(text_parts),
                    "time": (datetime.now() - self.start_time).total_seconds(),
                    "type": "speak"
                })

        if self._original_speak:
            return self._original_speak(speechSequence, *args, **kwargs)

    def _hooked_speakText(self, text, *args, **kwargs):
        if self.capturing and text:
            self.segments.append({
                "text": text,
                "time": (datetime.now() - self.start_time).total_seconds(),
                "type": "speakText"
            })

        if self._original_speakText:
            return self._original_speakText(text, *args, **kwargs)


class NVDANavigator(object):
    """Walks the virtual buffer using NVDA APIs."""

    @staticmethod
    def _speech_sequence_to_text(sequence):
        """Extract plain text from an NVDA speech sequence."""
        parts = [item.strip() for item in sequence if isinstance(item, str) and item.strip()]
        return " ".join(parts)

    @staticmethod
    def get_tree_interceptor():
        focus = api.getFocusObject()
        ti = treeInterceptorHandler.getTreeInterceptor(focus)
        if ti and isinstance(ti, browseMode.BrowseModeTreeInterceptor):
            return ti
        return None

    @staticmethod
    def get_full_text(ti):
        try:
            info = ti.makeTextInfo(textInfos.POSITION_ALL)
            return info.text or ""
        except Exception:
            return ""

    @staticmethod
    def read_line_by_line(ti, max_lines=150):
        """Walk down arrow through the document, return each line."""
        lines = []
        try:
            info = ti.makeTextInfo(textInfos.POSITION_FIRST)
            info.updateCaret()

            for _ in range(max_lines):
                caret = ti.makeTextInfo(textInfos.POSITION_CARET)
                caret.expand(textInfos.UNIT_LINE)
                text = ""
                try:
                    seq = list(speech.getTextInfoSpeech(
                        caret.copy(), unit=textInfos.UNIT_LINE, reason=_REASON_CARET,
                    ))
                    text = NVDANavigator._speech_sequence_to_text(seq)
                except Exception:
                    try:
                        seq = list(speech.speech.getTextInfoSpeech(
                            caret.copy(), unit=textInfos.UNIT_LINE, reason=_REASON_CARET,
                        ))
                        text = NVDANavigator._speech_sequence_to_text(seq)
                    except Exception as e:
                        log.debug("getTextInfoSpeech error: %s" % e)
                if not text:
                    text = caret.text.strip()
                if text:
                    lines.append(text)

                caret = ti.makeTextInfo(textInfos.POSITION_CARET)
                if not caret.move(textInfos.UNIT_LINE, 1):
                    break
                caret.updateCaret()
                time.sleep(0.02)

        except Exception as e:
            log.debug("read_line_by_line error: %s" % e)
        return lines

    @staticmethod
    def collect_elements(ti, element_type, max_items=100):
        """Navigate through all elements of a type (heading, link, etc.)."""
        elements = []
        try:
            info = ti.makeTextInfo(textInfos.POSITION_FIRST)
            info.updateCaret()

            for _ in range(max_items):
                try:
                    prev = ti.makeTextInfo(textInfos.POSITION_CARET)

                    ti._quickNavScript(
                        gesture=None,
                        itemType=element_type,
                        direction="next",
                        errorMessage="",
                        readUnit=None
                    )

                    time.sleep(NAV_DELAY)

                    caret = ti.makeTextInfo(textInfos.POSITION_CARET)
                    # If the caret didn't move, no more elements (or wrapped)
                    if caret.compareEndPoints(prev, "startToStart") == 0:
                        break

                    caret.expand(textInfos.UNIT_LINE)
                    text = ""
                    try:
                        seq = list(speech.getTextInfoSpeech(
                            caret.copy(), unit=textInfos.UNIT_LINE, reason=_REASON_CARET,
                        ))
                        text = NVDANavigator._speech_sequence_to_text(seq)
                    except Exception:
                        try:
                            seq = list(speech.speech.getTextInfoSpeech(
                                caret.copy(), unit=textInfos.UNIT_LINE, reason=_REASON_CARET,
                            ))
                            text = NVDANavigator._speech_sequence_to_text(seq)
                        except Exception as e:
                            log.debug("getTextInfoSpeech error: %s" % e)
                    if not text:
                        text = caret.text.strip()
                    if text:
                        elements.append(text[:300])

                except Exception as e:
                    log.debug("collect_elements error (%s): %s" % (element_type, e))
                    break

        except Exception:
            pass
        return elements


class FileWatcher(object):
    """Polls for command files from the Selenium crawler."""

    def __init__(self, speech_capture):
        self.speech_capture = speech_capture
        self.running = False
        self._thread = None
        self._last_command_time = None

    def start(self):
        if self.running:
            return
        self.running = True
        if not os.path.exists(COMM_DIR):
            os.makedirs(COMM_DIR)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self.speech_capture.capturing:
            self.speech_capture.stop()

    def _poll_loop(self):
        while self.running:
            try:
                if os.path.exists(COMMAND_FILE):
                    self._process_command()
            except Exception as e:
                log.error("FileWatcher error: %s" % e)
            time.sleep(POLL_INTERVAL)

    def _process_command(self):
        try:
            with open(COMMAND_FILE, "r", encoding="utf-8") as f:
                cmd = json.load(f)
        except (json.JSONDecodeError, IOError):
            return

        # Avoid re-processing the same command
        cmd_time = cmd.get("timestamp")
        if cmd_time == self._last_command_time:
            return
        self._last_command_time = cmd_time

        action = cmd.get("action")

        if action == "start":
            log.info("SpeechCapture: start")
            self.speech_capture.start()

        elif action == "stop":
            log.info("SpeechCapture: stop")
            segments = self.speech_capture.stop()
            self._write_result({"segments": segments})

        elif action == "navigate":
            log.info("SpeechCapture: navigate")
            self._do_navigate()

    def _do_navigate(self):
        """Actively walk the virtual buffer and capture everything."""
        # Give NVDA a moment to settle before looking for the virtual buffer.
        # Do NOT start speech capture yet — we don't want terminal readback.
        time.sleep(1)

        # Retry up to 30s for NVDA to build the virtual buffer.
        ti = None
        for _ in range(30):
            ti = NVDANavigator.get_tree_interceptor()
            if ti:
                break
            time.sleep(1)

        nav_data = {
            "full_text": "",
            "reading_order": [],
            "headings": [],
            "links": [],
            "landmarks": [],
            "form_fields": [],
            "images": [],
        }

        # Only start capturing speech once we're about to walk the page, so
        # segments reflect NVDA announcements during navigation only.
        self.speech_capture.start()

        if ti:
            try:
                nav_data["full_text"] = NVDANavigator.get_full_text(ti)
                nav_data["reading_order"] = NVDANavigator.read_line_by_line(ti)
                nav_data["headings"] = NVDANavigator.collect_elements(ti, "heading")
                nav_data["landmarks"] = NVDANavigator.collect_elements(ti, "landmark")
                nav_data["links"] = NVDANavigator.collect_elements(ti, "link")
                nav_data["form_fields"] = NVDANavigator.collect_elements(ti, "formField")
                nav_data["images"] = NVDANavigator.collect_elements(ti, "graphic")
            except Exception as e:
                log.error("Navigate error: %s" % e)
        else:
            log.warning("No virtual buffer available for navigate")

        # Stop speech capture
        segments = self.speech_capture.stop()

        self._write_result({
            "segments": segments,
            "navigation": nav_data,
        })

    def _write_result(self, result):
        result["timestamp"] = datetime.now().isoformat()
        try:
            with open(RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error("Failed to write result: %s" % e)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """NVDA global plugin — file-watcher for Selenium crawler coordination."""

    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.speech_capture = SpeechCapture()
        self.file_watcher = FileWatcher(self.speech_capture)
        # Auto-start the file watcher so the Selenium crawler can
        # communicate immediately after launching NVDA.
        self.file_watcher.start()
        log.info("Tranco file watcher auto-started")

    def terminate(self):
        self.file_watcher.stop()

    @script(
        description="Toggle the Tranco crawler file watcher on/off",
        gesture="kb:NVDA+shift+t"
    )
    def script_toggleFileWatcher(self, gesture):
        if self.file_watcher.running:
            self.file_watcher.stop()
            ui.message("Tranco file watcher stopped")
        else:
            self.file_watcher.start()
            ui.message("Tranco file watcher started")
