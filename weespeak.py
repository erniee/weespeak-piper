# -*- coding: utf-8 -*-

#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

#
# Copyright (c) Alexander Schnaidt <alex.schnaidt@gmail.com>
#
# 0.2 : pluggable TTS backends (Amazon Polly, Google Cloud TTS, Piper,
#       espeak-ng). Synthesis and playback moved off the WeeChat event
#       loop into a forked child via hook_process, with a serial queue
#       so messages do not talk over each other. Settings are now
#       WeeChat plugin options and persist across reloads.
#

SCRIPT_NAME    = "weespeak"
SCRIPT_AUTHOR  = "Alexander Schnaidt <alex.schnaidt@gmail.com>"
SCRIPT_VERSION = "0.2"
SCRIPT_LICENSE = "GPL3"
SCRIPT_DESC    = "Speaks messages in the current irc-buffer using a local or cloud TTS engine."
SCRIPT_COMMAND = "weespeak"

import collections
import os
import re
import shlex
import shutil
import subprocess
import tempfile

try:
    import weechat
    from weechat import WEECHAT_RC_OK as W_OK
except ImportError:
    raise ImportError("Load this script from inside weechat (http://weechat.org). "
                      "Run '/python load /path/to/script/weespeak.py' from weechat.")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

DEFAULTS = {
    "backend":            ("polly",
                           "tts backend: polly, google, piper or espeak"),
    "syntax":             ("{who} says {what}",
                           "sentence template, placeholders: {who} {what} {where}"),
    "max_message_length": ("500",
                           "messages longer than this many characters are skipped"),
    "muted":              ("",
                           "comma separated list of muted nicks (managed by /weespeak mute)"),
    "speak_urls":         ("off",
                           "off: replace urls with the word 'link' instead of spelling them out"),
    "queue_size":         ("10",
                           "maximum number of pending messages, oldest are dropped"),
    "player":             ("",
                           "playback command, {file} is substituted; empty means auto-detect"),

    "polly_voice":        ("Matthew", "polly voice id, see: aws polly describe-voices"),
    "polly_engine":       ("neural",  "polly engine: standard, neural, long-form or generative"),
    "polly_region":       ("",        "aws region, empty uses the default from your aws config"),

    "google_voice":       ("en-US-Neural2-D", "google cloud tts voice name"),
    "google_language":    ("en-US",           "google cloud tts language code"),

    "piper_binary":       ("piper", "path to the piper executable"),
    "piper_model":        ("",      "path to the piper .onnx voice model (required for piper)"),

    "espeak_binary":      ("espeak-ng", "path to espeak or espeak-ng"),
    "espeak_speed":       ("165",       "espeak words per minute"),
}

# Snapshot of the options above. The child process inherits this dict through
# fork(), which is why the child never calls the weechat API itself.
cfg = {}
muted = set()

URL_RE = re.compile(r"\w+://\S+|www\.\S+")


def load_config():
    global muted
    for name in DEFAULTS:
        cfg[name] = weechat.config_get_plugin(name)

    try:
        cfg["max_message_length"] = int(cfg["max_message_length"])
    except ValueError:
        cfg["max_message_length"] = 500

    try:
        cfg["queue_size"] = max(1, int(cfg["queue_size"]))
    except ValueError:
        cfg["queue_size"] = 10

    muted = set(n for n in cfg["muted"].split(",") if n)
    _resize_queue(cfg["queue_size"])


def config_cb(data, option, value):
    load_config()
    return W_OK


def save_muted():
    weechat.config_set_plugin("muted", ",".join(sorted(muted)))


# ---------------------------------------------------------------------------
# irc helpers
# ---------------------------------------------------------------------------

def own_nick(server):
    """this users nick"""
    return weechat.info_get("irc_nick", server)


def buffer_current():
    """current buffer pointer as string"""
    return weechat.current_buffer()


def buffer(server, channel):
    """buffer pointer as string"""
    return weechat.info_get("irc_buffer", ",".join([server, channel]))


def parse_message(data, signal, signal_data):
    """parse the raw irc line and return a dict"""
    hm = weechat.info_get_hashtable("irc_message_parse", {"message": signal_data})
    hm["server"] = signal.split(",")[0]

    # 'text' is provided by weechat >= 1.3 and is what we actually want.
    # The old lstrip(channel) trick was subtly wrong: str.lstrip treats its
    # argument as a set of characters, not a prefix, so a message in #weechat
    # would have leading 'w', 'e', 'c', 'h', 'a' and 't' characters eaten.
    text = hm.get("text")
    if not text:
        arguments = hm.get("arguments", "")
        text = arguments.split(" :", 1)[1] if " :" in arguments else arguments

    hm["message"] = weechat.string_remove_color(text, "")
    return hm


# ---------------------------------------------------------------------------
# speech queue
# ---------------------------------------------------------------------------

queue = collections.deque(maxlen=10)
speaking = False
pending = ""


def _resize_queue(size):
    global queue
    if queue.maxlen != size:
        queue = collections.deque(queue, maxlen=size)


def enqueue(sentence):
    queue.append(sentence)
    pump()


def pump():
    """start the next synthesis if the child slot is free"""
    global speaking, pending

    if speaking or not queue:
        return

    pending = queue.popleft()
    speaking = True
    # timeout 0 == no timeout; the child lives for synthesis + playback
    weechat.hook_process("func:child_speak", 0, "speak_cb", pending)


def speak_cb(data, command, return_code, out, err):
    global speaking

    if return_code == weechat.WEECHAT_HOOK_PROCESS_RUNNING:
        return W_OK

    message = (out or "").strip() or (err or "").strip()
    if message:
        weechat.prnt("", "{}weespeak: {}".format(weechat.prefix("error"), message))

    speaking = False
    pump()
    return W_OK


# ---------------------------------------------------------------------------
# synthesis, runs in the forked child
# ---------------------------------------------------------------------------

PLAYERS = [
    ("mpv",    "mpv --really-quiet --no-video --audio-display=no {file}"),
    ("ffplay", "ffplay -nodisp -autoexit -loglevel quiet {file}"),
    ("mpg123", "mpg123 -q {file}"),
    ("afplay", "afplay {file}"),
    ("paplay", "paplay {file}"),
    ("aplay",  "aplay -q {file}"),
]


def play(path):
    template = cfg.get("player") or ""

    if not template:
        wav = path.endswith(".wav")
        for binary, default in PLAYERS:
            # aplay and paplay cannot decode mp3
            if binary in ("aplay", "paplay") and not wav:
                continue
            if shutil.which(binary):
                template = default
                break

    if not template:
        return "no audio player found, install mpv or ffmpeg, or set the 'player' option"

    argv = [a.replace("{file}", path) for a in shlex.split(template)]
    result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return "player failed: {}".format(result.stderr.decode("utf-8", "replace").strip()[:200])
    return ""


def synth_polly(sentence, path):
    import boto3

    kwargs = {}
    if cfg["polly_region"]:
        kwargs["region_name"] = cfg["polly_region"]

    response = boto3.client("polly", **kwargs).synthesize_speech(
        Text=sentence,
        VoiceId=cfg["polly_voice"],
        Engine=cfg["polly_engine"],
        OutputFormat="mp3",
    )
    with open(path, "wb") as handle:
        handle.write(response["AudioStream"].read())


def synth_google(sentence, path):
    from google.cloud import texttospeech as tts

    response = tts.TextToSpeechClient().synthesize_speech(
        input=tts.SynthesisInput(text=sentence),
        voice=tts.VoiceSelectionParams(
            language_code=cfg["google_language"],
            name=cfg["google_voice"],
        ),
        audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
    )
    with open(path, "wb") as handle:
        handle.write(response.audio_content)


def synth_piper(sentence, path):
    if not cfg["piper_model"]:
        raise RuntimeError("set the 'piper_model' option to a .onnx voice file")

    subprocess.run(
        [cfg["piper_binary"], "--model", cfg["piper_model"], "--output_file", path],
        input=sentence.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )


def child_speak(sentence):
    """runs in a forked child, returns an error string or empty on success"""
    backend = cfg.get("backend", "polly")

    if backend == "espeak":
        try:
            subprocess.run(
                [cfg["espeak_binary"], "-s", cfg["espeak_speed"], "--", sentence],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return ""
        except Exception as error:
            return "espeak failed: {}".format(error)

    synths = {
        "polly":  (synth_polly,  ".mp3"),
        "google": (synth_google, ".mp3"),
        "piper":  (synth_piper,  ".wav"),
    }
    if backend not in synths:
        return "unknown backend '{}'".format(backend)

    synth, suffix = synths[backend]
    handle, path = tempfile.mkstemp(prefix="weespeak-", suffix=suffix)
    os.close(handle)

    try:
        synth(sentence, path)
        return play(path)
    except Exception as error:
        return "{} synthesis failed: {}: {}".format(backend, type(error).__name__, error)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# signal handling
# ---------------------------------------------------------------------------

def speak_out(data, signal, signal_data):
    msg = parse_message(data, signal, signal_data)

    if not msg.get("message"):
        return W_OK

    # filter out messages that would take too long to synthesize
    if len(msg["message"]) > cfg["max_message_length"]:
        return W_OK

    # you don't want to hear your own messages
    if msg["nick"] == own_nick(msg["server"]):
        return W_OK

    # don't speak out if nick is in ignore list
    if msg["nick"] in muted:
        return W_OK

    # only speak out the current buffer
    if buffer_current() != buffer(msg["server"], msg["channel"]):
        return W_OK

    text = msg["message"]
    if cfg["speak_urls"] != "on":
        text = URL_RE.sub("link", text)

    enqueue(cfg["syntax"].format(who=msg["nick"], what=text, where=msg["channel"]))
    return W_OK


# ---------------------------------------------------------------------------
# /weespeak
# ---------------------------------------------------------------------------

def mute(nicks):
    muted.update(nicks)
    save_muted()


def unmute(nicks):
    muted.difference_update(nicks)
    save_muted()


def cmd(data, from_buffer, args):
    arguments = args.split()

    if not arguments:
        weechat.prnt("", "weespeak: backend={} voice={} queued={}".format(
            cfg["backend"],
            cfg["polly_voice"] if cfg["backend"] == "polly" else cfg["google_voice"],
            len(queue)))
        return W_OK

    command = arguments[0]

    if command == "mute":
        mute(arguments[1:])
    elif command == "unmute":
        unmute(arguments[1:])
    elif command == "list_muted":
        weechat.prnt("", "muted nicks: {}".format(" ".join(sorted(muted)) or "(none)"))
    elif command == "say":
        enqueue(" ".join(arguments[1:]) or "weespeak test")
    elif command == "stop":
        queue.clear()
    else:
        weechat.prnt("", "unknown command: '{}'".format(command))
        return weechat.WEECHAT_RC_ERROR

    return W_OK


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

weechat.register(SCRIPT_NAME,
                 SCRIPT_AUTHOR,
                 SCRIPT_VERSION,
                 SCRIPT_LICENSE,
                 SCRIPT_DESC, "", "")

for option, (default, description) in DEFAULTS.items():
    if not weechat.config_is_set_plugin(option):
        weechat.config_set_plugin(option, default)
    weechat.config_set_desc_plugin(option, description)

load_config()

weechat.hook_config("plugins.var.python." + SCRIPT_NAME + ".*", "config_cb", "")

# run speak_out() AFTER the message got processed => irc_in2_privmsg;
# run BEFORE => irc_in_privmsg
weechat.hook_signal("*,irc_in2_privmsg", "speak_out", "")

weechat.hook_command(SCRIPT_COMMAND,
                     "adjust the weespeak configuration",
                     "[mute | unmute] [nick(s)] | [list_muted] | [say <text>] | [stop]",
                     "list_muted: prints out nicks that are ignored by weespeak\n"
                     "mute      : puts a nick / list of nicks on ignore\n"
                     "unmute    : removes a nick / list of nicks from ignore\n"
                     "say       : speaks the given text, useful for testing a backend\n"
                     "stop      : drops everything still queued\n\n"
                     " Settings live in plugins.var.python.weespeak.*, see /set weespeak\n\n"
                     " Example:\n"
                     "  /weespeak mute elvis\n"
                     "   puts the nick 'elvis' on the ignore list\n\n"
                     "  /weespeak say hello there\n"
                     "   checks that the configured backend works\n",
                     " || mute %(nicks)"
                     " || unmute %(nicks)"
                     " || list_muted"
                     " || say"
                     " || stop",
                     "cmd", "")
