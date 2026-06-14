"""yanflix/app/vault_manager.py

Utilities for scanning voice vault directories:
- legacy:   characters/*.wav
- new:      characters/global_roster/<char>/avatar_monologue.wav
- new:      characters/shows/<show_slug>/<char>/avatar_monologue.wav

This keeps UI code cleaner and centralizes folder naming rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


RANK_REF_FILENAME = "avatar_monologue.wav"


def _slugify(value: str) -> str:
    # local small helper to keep consistent naming
    import re

    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "project"


@dataclass(frozen=True)
class VoiceProfile:
    character_id: str
    reference_wav: Path
    domain: Literal["global", "show", "legacy"]
    show_slug: str | None = None


class VoiceVaultScanner:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or Path(__file__).resolve().parent.parent
        self.characters_dir = self.root_dir / "characters"
        self.legacy_dir = self.characters_dir
        self.global_dir = self.characters_dir / "global_roster"
        self.shows_dir = self.characters_dir / "shows"

        self.global_dir.mkdir(parents=True, exist_ok=True)
        self.shows_dir.mkdir(parents=True, exist_ok=True)

    def scan_legacy_flat_wavs(self) -> list[VoiceProfile]:
        profiles: list[VoiceProfile] = []
        for wav in self.legacy_dir.glob("*.wav"):
            if not wav.is_file():
                continue
            profiles.append(
                VoiceProfile(
                    character_id=wav.stem,
                    reference_wav=wav,
                    domain="legacy",
                    show_slug=None,
                )
            )
        return sorted(profiles, key=lambda p: p.character_id.lower())

    def scan_global_roster(self) -> list[VoiceProfile]:
        profiles: list[VoiceProfile] = []
        for char_dir in self.global_dir.iterdir():
            if not char_dir.is_dir():
                continue
            ref = char_dir / RANK_REF_FILENAME
            if ref.exists():
                profiles.append(
                    VoiceProfile(
                        character_id=char_dir.name,
                        reference_wav=ref,
                        domain="global",
                        show_slug=None,
                    )
                )
        return sorted(profiles, key=lambda p: p.character_id.lower())

    def scan_show_roster(self, show_slug: str) -> list[VoiceProfile]:
        show_slug = _slugify(show_slug)
        show_dir = self.shows_dir / show_slug
        profiles: list[VoiceProfile] = []
        if not show_dir.exists():
            return profiles
        for char_dir in show_dir.iterdir():
            if not char_dir.is_dir():
                continue
            ref = char_dir / RANK_REF_FILENAME
            if ref.exists():
                profiles.append(
                    VoiceProfile(
                        character_id=char_dir.name,
                        reference_wav=ref,
                        domain="show",
                        show_slug=show_slug,
                    )
                )
        return sorted(profiles, key=lambda p: p.character_id.lower())

    def list_show_slugs(self) -> list[str]:
        return sorted(
            [d.name for d in self.shows_dir.iterdir() if d.is_dir()],
            key=lambda s: s.lower(),
        )

    def iter_profiles(self, domain: str, show_slug: str | None = None) -> list[VoiceProfile]:
        if domain == "global":
            return self.scan_global_roster()
        if domain == "show":
            if not show_slug:
                raise ValueError("show_slug is required for show domain")
            return self.scan_show_roster(show_slug)
        if domain == "legacy":
            return self.scan_legacy_flat_wavs()
        raise ValueError(f"Unknown domain: {domain}")

