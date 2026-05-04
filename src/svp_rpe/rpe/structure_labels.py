"""rpe/structure_labels.py - Heuristic section label assignment.

Assigns Intro/Verse/Chorus/Bridge/Outro labels based on energy profiles.
Not exact - better than section_01 fixed labels.
"""
from __future__ import annotations

from typing import List


def assign_labels(
    section_rms: List[float],
    total_sections: int,
) -> List[str]:
    """Assign section labels based on energy (RMS) profile.

    Heuristic rules:
    - First section -> Intro
    - Last section -> Outro
    - Highest energy sections -> Chorus
    - Lowest energy sections -> Bridge (if not first/last)
    - Others -> Verse
    """
    if total_sections == 0:
        return []
    if total_sections == 1:
        return ["Full"]

    def _ranked_middle_sections(mid_indices: list[int]) -> list[tuple[int, float]]:
        mid_rms = [(i, section_rms[i]) for i in mid_indices]
        return sorted(mid_rms, key=lambda x: x[1], reverse=True)

    def _assign_choruses(
        labels: list[str],
        sorted_by_rms: list[tuple[int, float]],
        mid_indices: list[int],
    ) -> None:
        chorus_limit = min(2, len(mid_indices) // 2 + 1)
        for idx, _ in sorted_by_rms[:chorus_limit]:
            labels[idx] = "Chorus"

    def _assign_bridge(
        labels: list[str],
        sorted_by_rms: list[tuple[int, float]],
    ) -> None:
        for idx, _ in reversed(sorted_by_rms):
            if labels[idx] == "":
                labels[idx] = "Bridge"
                return

    def _assign_verses(labels: list[str], mid_indices: list[int]) -> None:
        verse_count = 0
        for i in mid_indices:
            if labels[i] == "":
                verse_count += 1
                labels[i] = f"Verse{verse_count}" if verse_count > 1 else "Verse"

    labels = [""] * total_sections

    # First and last
    labels[0] = "Intro"
    labels[-1] = "Outro"

    if total_sections == 2:
        return labels

    # For middle sections, rank by energy
    mid_indices = list(range(1, total_sections - 1))
    if not mid_indices:
        return labels

    sorted_by_rms = _ranked_middle_sections(mid_indices)

    # Top energy -> Chorus (up to 2)
    _assign_choruses(labels, sorted_by_rms, mid_indices)

    # Lowest energy among remaining -> Bridge (max 1)
    _assign_bridge(labels, sorted_by_rms)

    # Fill remaining with Verse
    _assign_verses(labels, mid_indices)

    return labels
