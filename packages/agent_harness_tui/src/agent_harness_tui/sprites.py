"""Animated sprite registry for the running indicator.

Each sprite is a frozen sequence of width-stable frames played at its own
FPS. The TUI picks one at random when a turn begins; the status bar reads
the current frame from elapsed wall-clock time, so the animation is purely
a function of (sprite, elapsed) — no per-frame state to track.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

WIDTH = 9

#     ================================
# --> Helper funcs
#     ================================


@dataclass(frozen=True)
class Sprite:
    name: str
    fps: float
    frames: tuple[str, ...]


def pick() -> Sprite:
    """Return a uniformly-random sprite from the registry."""
    return random.choice(SPRITES)


def pick_different(current: Sprite | None) -> Sprite:
    """Return a random sprite that is not `current`.

    Used by the animation rotator so two identical sprites never play
    back-to-back. Falls back to `pick()` when the registry has one entry.
    """
    if current is None or len(SPRITES) == 1:
        return random.choice(SPRITES)

    others = tuple(s for s in SPRITES if s is not current)

    return random.choice(others)


def cycle_seconds(sprite: Sprite) -> float:
    """Duration of one full play of `sprite`, in seconds."""
    return len(sprite.frames) / sprite.fps


def frame_at(sprite: Sprite, elapsed_s: float) -> str:
    """Return the frame `sprite` should show at `elapsed_s` seconds in."""
    idx = int(elapsed_s * sprite.fps) % len(sprite.frames)

    return sprite.frames[idx]

#     ================================
# --> Sprite registry
#     ================================
# Every frame must be exactly WIDTH cells wide. Misaligned frames cause the
# status bar to jitter horizontally as the renderer pads or truncates.

SPRITES: tuple[Sprite, ...] = (

    Sprite('pacman', 6.0, (
        'ᗧ········',
        ' ᗤ·······',
        '  ᗧ······',
        '   ᗤ·····',
        '    ᗧ····',
        '     ᗤ···',
        '      ᗧ··',
        '       ᗤ·',
        '        ᗧ',
    )),

    Sprite('comet', 7.0, (
        '✦········',
        '─✦·······',
        '╌─✦······',
        ' ╌─✦·····',
        '  ╌─✦····',
        '   ╌─✦···',
        '    ╌─✦··',
        '     ╌─✦·',
        '      ╌─✦',
    )),

    Sprite('rocket', 7.0, (
        '>········',
        '~>·······',
        '=~>······',
        ' =~>·····',
        '  =~>····',
        '   =~>···',
        '    =~>··',
        '     =~>·',
        '      =~>',
    )),

    Sprite('snake', 5.0, (
        '~∽~······',
        ' ∽~∽·····',
        '  ~∽~····',
        '   ∽~∽···',
        '    ~∽~··',
        '     ∽~∽·',
        '      ~∽~',
    )),

    Sprite('runner', 7.0, (
        'ƪ········',
        ' ɼ·······',
        '  ƪ······',
        '   ɼ·····',
        '    ƪ····',
        '     ɼ···',
        '      ƪ··',
        '       ɼ·',
        '        ƪ',
    )),

    Sprite('butterfly', 4.0, (
        ')(·······',
        '·()······',
        '··)(·····',
        '···()····',
        '····)(···',
        '·····()··',
        '······)(·',
        '·······()',
    )),

    Sprite('lightning', 9.0, (
        'ϟ········',
        '·ϟ·······',
        '··ϟ······',
        '···ϟ·····',
        '····ϟ····',
        '·····ϟ···',
        '······ϟ··',
        '·······ϟ·',
        '········ϟ',
    )),


    Sprite('tumbleweed', 6.0, (
        'o········',
        ' O·······',
        '  o······',
        '   O·····',
        '    o····',
        '     O···',
        '      o··',
        '       O·',
        '        o',
    )),

    Sprite('helicopter', 7.0, (
        '-O>······',
        ' |O>·····',
        '  -O>····',
        '   |O>···',
        '    -O>··',
        '     |O>·',
        '      -O>',
    )),

    Sprite('arrow', 8.0, (
        '=>·······',
        ' =>······',
        '  =>·····',
        '   =>····',
        '    =>···',
        '     =>··',
        '      =>·',
        '       =>',
    )),

    Sprite('boomerang', 6.0, (
        '~········',
        ' \\·······',
        '  |······',
        '   /·····',
        '    ~····',
        '     \\···',
        '      |··',
        '       /·',
        '        ~',
    )),

    Sprite('bouncing-ball', 6.0, (
        '.········',
        ' o·······',
        '  O······',
        '   o·····',
        '    .····',
        '     o···',
        '      O··',
        '       o·',
        '        .',
    )),

    Sprite('shooting-star', 7.0, (
        '✩········',
        '⋯✩·······',
        ' ⋯✩······',
        '  ⋯✩·····',
        '   ⋯✩····',
        '    ⋯✩···',
        '     ⋯✩··',
        '      ⋯✩·',
        '       ⋯✩',
    )),

    Sprite('wave', 4.0, (
        '⌒~~~~~~~~',
        '~⌒~~~~~~~',
        '~~⌒~~~~~~',
        '~~~⌒~~~~~',
        '~~~~⌒~~~~',
        '~~~~~⌒~~~',
        '~~~~~~⌒~~',
        '~~~~~~~⌒~',
        '~~~~~~~~⌒',
    )),

    Sprite('pogo', 5.0, (
        '!········',
        ' i·······',
        '  !······',
        '   i·····',
        '    !····',
        '     i···',
        '      !··',
        '       i·',
        '        !',
    )),

    Sprite('ufo', 6.0, (
        '<=o=>····',
        ' <=O=>···',
        '  <=o=>··',
        '   <=O=>·',
        '    <=o=>',
    )),

    Sprite('ant', 6.0, (
        'ʕ·ʔ······',
        ' ʕ·ʔ·····',
        '  ʕ·ʔ····',
        '   ʕ·ʔ···',
        '    ʕ·ʔ··',
        '     ʕ·ʔ·',
        '      ʕ·ʔ',
    )),

    Sprite('ghost', 4.0, (
        'ᗜ········',
        ' ᗯ·······',
        '  ᗜ······',
        '   ᗯ·····',
        '    ᗜ····',
        '     ᗯ···',
        '      ᗜ··',
        '       ᗯ·',
        '        ᗜ',
    )),

    # --- comet-style: head glyph with a fading trail ---

    Sprite('meteor', 7.0, (
        '◉········',
        '.◉·······',
        '..◉······',
        ':..◉·····',
        ' :..◉····',
        '  :..◉···',
        '   :..◉··',
        '    :..◉·',
        '     :..◉',
    )),

    Sprite('bullet', 8.0, (
        '●········',
        '═●·······',
        '══●······',
        ' ══●·····',
        '  ══●····',
        '   ══●···',
        '    ══●··',
        '     ══●·',
        '      ══●',
    )),

    Sprite('torpedo', 6.0, (
        '►········',
        '≈►·······',
        '≈≈►······',
        ' ≈≈►·····',
        '  ≈≈►····',
        '   ≈≈►···',
        '    ≈≈►··',
        '     ≈≈►·',
        '      ≈≈►',
    )),

    Sprite('photon', 8.0, (
        '○········',
        '─○·······',
        '──○······',
        ' ──○·····',
        '  ──○····',
        '   ──○···',
        '    ──○··',
        '     ──○·',
        '      ──○',
    )),

    Sprite('spark', 7.0, (
        '✧········',
        ',✧·······',
        '·,✧······',
        ' ·,✧·····',
        '  ·,✧····',
        '   ·,✧···',
        '    ·,✧··',
        '     ·,✧·',
        '      ·,✧',
    )),

    Sprite('flare', 6.0, (
        '⊛········',
        '=⊛·······',
        '─=⊛······',
        ' ─=⊛·····',
        '  ─=⊛····',
        '   ─=⊛···',
        '    ─=⊛··',
        '     ─=⊛·',
        '      ─=⊛',
    )),

    Sprite('ember', 6.0, (
        '◆········',
        '·◆·······',
        '.·◆······',
        ' .·◆·····',
        '  .·◆····',
        '   .·◆···',
        '    .·◆··',
        '     .·◆·',
        '      .·◆',
    )),

)


# Fail-fast width check at import time. Catches any frame typo before the
# user ever sees a jittery status bar.
for _sprite in SPRITES:
    for _frame in _sprite.frames:
        if len(_frame) != WIDTH:
            raise ValueError(
                f"Sprite {_sprite.name!r} frame {_frame!r} has width "
                f"{len(_frame)}, expected {WIDTH}"
            )
