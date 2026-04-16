"""rpe/structure_labels.py — Heuristic section label assignment.

Assigns Intro/Verse/Chorus/Bridge/Outro labels based on energy profiles.
Not exact — better than section_01 fixed labels.
"""
from __future__ import annotations

from typing import List



def assign_labels(
    section_rms: List[float],
    total_sections: int,
) -> List[str]:
    """Assign section labels based on energy (RMS) profile.

    Heuristic rules:
    - First section → Intro
    - Last section → Outro
    - Highest energy sections → Chorus
    - Lowest energy sections → Bridge (if not first/last)
    - Others → Verse
    """
    if total_sections == 0:
        return []
    if total_sections == 1:
        return ["Full"]

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

    mid_rms = [(i, section_rms[i]) for i in mid_indices]
    sorted_by_rms = sorted(mid_rms, key=lambda x: x[1], reverse=True)

    # Top energy → Chorus (up to 2)
    chorus_count = 0
    for idx, _ in sorted_by_rms:
        if chorus_count < min(2, len(mid_indices) // 2 + 1):
            labels[idx] = "Chorus"
            chorus_count += 1

    # Lowest energy among remaining → Bridge (max 1)
    bridge_assigned = False
    for idx, _ in reversed(sorted_by_rms):
        if labels[idx] == "" and not bridge_assigned:
            labels[idx] = "Bridge"
            bridge_assigned = True

    # Fill remaining with Verse
    verse_count = 0
    for i in mid_indices:
        if labels[i] == "":
            verse_count += 1
            labels[i] = f"Verse{verse_count}" if verse_count > 1 else "Verse"

    return labels
