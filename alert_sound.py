"""Generate and play local Parallel notification sounds.

Synthesizes short alert tones and handles playback for application events;
generated sounds are runtime data rather than published source assets.
Parallel has no spoken voice lines: only plain tones are generated.
"""

from __future__ import annotations

import math
import random
import struct
import threading
import wave
import winsound
from pathlib import Path

import config


def play_alert(event: str = "info"):
    if not getattr(config, "ALERT_SOUND_ENABLED", True):
        return

    def _play():
        try:
            path = generated_alert_path(event)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            try:
                winsound.MessageBeep()
            except Exception:
                pass

    threading.Thread(target=_play, daemon=True).start()


def play_voice_line(phrase: str, cache_key: str = "line"):
    """Kept only so older callers keep working; Parallel does not speak."""
    return None


def generated_alert_path(event: str) -> Path:
    style = getattr(config, "ALERT_SOUND_STYLE", "Random")
    if style == "Custom File":
        custom = Path(str(getattr(config, "ALERT_SOUND_CUSTOM_PATH", ""))).expanduser()
        if custom.exists() and custom.suffix.lower() == ".wav":
            return custom
    if style == "Random" or style not in generated_styles():
        # Also covers saved settings that still name the removed voice style.
        style = random.choice(generated_styles())
    volume = max(0.0, min(1.0, float(getattr(config, "ALERT_SOUND_VOLUME", 0.45))))
    safe_name = f"{style.lower().replace(' ', '_')}_{event}_{int(volume * 100)}.wav"
    out_dir = Path(getattr(config, "GENERATED_SOUNDS_DIR", "generated_sounds"))
    out_dir.mkdir(exist_ok=True)
    path = out_dir / safe_name
    if not path.exists():
        _write_alert_wav(path, style, event, volume)
    return path


def generated_styles() -> list[str]:
    return [
        "Void Chime",
        "Trade Ping",
        "Soft Pulse",
        "Credit Tick",
        "Orokin Bell",
        "Relic Spark",
        "Cephalon Ping",
        "Market Bell",
        "Quiet Confirm",
    ]


def _write_alert_wav(path: Path, style: str, event: str, volume: float):
    patterns = {
        "Void Chime": [(392, 0.09), (587, 0.10), (880, 0.16)],
        "Trade Ping": [(660, 0.06), (990, 0.07), (1320, 0.08)],
        "Soft Pulse": [(330, 0.12), (440, 0.14)],
        "Credit Tick": [(1046, 0.04), (1318, 0.04), (1568, 0.08)],
        "Orokin Bell": [(262, 0.08), (523, 0.11), (784, 0.18), (1046, 0.12)],
        "Relic Spark": [(1480, 0.035), (1760, 0.04), (2217, 0.08), (1175, 0.08)],
        "Cephalon Ping": [(740, 0.055), (554, 0.055), (932, 0.11)],
        "Market Bell": [(880, 0.05), (1175, 0.05), (880, 0.12)],
        "Quiet Confirm": [(494, 0.08), (659, 0.12)],
    }
    event_shift = {
        "success": 1.12,
        "trade": 1.0,
        "warning": 0.72,
        "error": 0.55,
        "info": 0.92,
    }.get(event, 1.0)

    sample_rate = 44100
    samples: list[int] = []
    for freq, seconds in patterns.get(style, patterns["Void Chime"]):
        freq *= event_shift
        tone_samples = int(sample_rate * seconds)
        for i in range(tone_samples):
            t = i / sample_rate
            attack = min(1.0, i / max(1, int(sample_rate * 0.012)))
            release = min(1.0, (tone_samples - i) / max(1, int(sample_rate * 0.035)))
            env = min(attack, release)
            wave_a = math.sin(2 * math.pi * freq * t)
            wave_b = 0.28 * math.sin(2 * math.pi * freq * 1.5 * t)
            value = int(32767 * volume * env * 0.45 * (wave_a + wave_b))
            samples.append(max(-32767, min(32767, value)))
        samples.extend([0] * int(sample_rate * 0.035))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
