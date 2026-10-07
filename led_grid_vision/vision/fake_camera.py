"""FakeCamera: synthetic frames of an 8x8 red LED grid. Deterministic for a seed."""

import numpy as np
import cv2

from vision.camera import BaseCamera

N = 8
PITCH = 32  # grid-space pixels per cell before warping
BACKGROUND_BANK = 4  # pre-rendered noisy backgrounds outside the module box
# Physical module corners tl, tr, bl, br in the image: a skewed quad about 120 px wide,
# wider at the bottom (lid tilted down toward the palm rest).
DEFAULT_QUAD = ((585, 300), (695, 302), (570, 418), (712, 420))


class FakeCamera(BaseCamera):
    def __init__(self, rows=None, size=(1280, 720), quad=DEFAULT_QUAD, rotation=0, mirrored=False,
                 led_radius_frac=0.3, blur_sigma=1.0, on_level=200.0, off_level=30.0, ambient=20.0,
                 noise_sigma=2.0, glow_frac=0.08, banding=0.0, gain=1.0, occluded=None,
                 blocked=False, seed=0) -> None:
        if rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180 or 270")
        self.rows = list(rows) if rows is not None else ["0" * N] * N
        self.size = size
        self.quad = quad
        self.rotation = rotation
        self.mirrored = mirrored
        self.led_radius_frac = led_radius_frac
        self.blur_sigma = blur_sigma
        self.on_level, self.off_level, self.ambient = on_level, off_level, ambient
        self.noise_sigma = noise_sigma
        self.glow_frac = glow_frac
        self.banding = banding
        self.gain = gain
        self.occluded = set(occluded or ())
        self.blocked = blocked
        self._rng = np.random.default_rng(seed)
        self._locked = False
        self._bg_key = None
        self._bg = None

    # interface
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def lock_exposure(self) -> bool:
        self._locked = True
        return True

    def set_rows(self, rows: list[str]) -> None:
        self.rows = list(rows)

    def info(self) -> dict:
        w, h = self.size
        return {"source": "fake", "backend": "fake", "width": w, "height": h, "fps": 30.0,
                "exposure_locked": self._locked, "exposure": None}

    def grab(self, n: int) -> list[np.ndarray]:
        return [self._render() for _ in range(n)]

    # rendering
    def _row_factors(self) -> np.ndarray:
        """Multiplex banding: dim a random subset of rows, normalised so the expectation is 1."""
        if self.banding <= 0:
            return np.ones(N)
        dim = 0.5
        hit = self._rng.random(N) < self.banding
        return np.where(hit, 1.0 - dim, 1.0) / (1.0 - dim * self.banding)

    def _render(self) -> np.ndarray:
        w, h = self.size
        if self.blocked:
            return np.full((h, w, 3), 3, np.uint8)

        side = N * PITCH
        yy, xx = np.mgrid[0:side, 0:side].astype(np.float32)
        cell_r, cell_c = (yy // PITCH).astype(int), (xx // PITCH).astype(int)
        dy = yy - (cell_r * PITCH + PITCH / 2 - 0.5)
        dx = xx - (cell_c * PITCH + PITCH / 2 - 0.5)
        in_disc = (dx * dx + dy * dy) <= (self.led_radius_frac * PITCH) ** 2

        lit = np.array([[ch == "1" for ch in row] for row in self.rows], bool)
        for r, c in self.occluded:
            lit[r, c] = False
        level = (self.on_level - self.off_level) * self._row_factors()[:, None] * lit
        led = np.where(in_disc, level[cell_r, cell_c], 0.0).astype(np.float32)
        if self.glow_frac > 0:
            glow = cv2.GaussianBlur(led, (0, 0), 0.6 * PITCH)
            led += self.glow_frac * glow / _single_glow_peak(self.led_radius_frac)
        dark = np.zeros((side, side), np.float32)
        for r, c in self.occluded:
            dark[r * PITCH:(r + 1) * PITCH, c * PITCH:(c + 1) * PITCH] = 1.0

        # Physical rotation of the module (clockwise), then onto the image quad.
        k = -(self.rotation // 90)
        led, dark = np.rot90(led, k).copy(), np.rot90(dark, k).copy()
        # Everything outside the quad's padded box is a static ambient gradient, so only the box
        # is rendered per frame.
        q = np.float32(self.quad)
        pad = int(4 * self.blur_sigma) + 4
        x0, x1 = max(0, int(q[:, 0].min()) - pad), min(w, int(q[:, 0].max()) + pad + 1)
        y0, y1 = max(0, int(q[:, 1].min()) - pad), min(h, int(q[:, 1].max()) + pad + 1)
        gradient = self.ambient * (0.8 + 0.4 * np.linspace(0, 1, w, dtype=np.float32))[None, :]
        img = self._background(gradient)[self._rng.integers(BACKGROUND_BANK)].copy()
        if x1 > x0 and y1 > y0:
            src = np.float32([[0, 0], [side, 0], [0, side], [side, side]])
            m = cv2.getPerspectiveTransform(src, q - np.float32([x0, y0]))
            warp = lambda im: cv2.warpPerspective(im, m, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR)
            led_img = warp(led)
            module = warp(np.ones((side, side), np.float32))
            dark_img = warp(dark)
            base = gradient[:, x0:x1] * (1 - module) + self.off_level * module
            base = base * (1 - dark_img)
            led_img = led_img * (1 - dark_img)  # an occluded cell also hides neighbour glow
            red = base + led_img
            bleed = base + 0.08 * led_img + 0.5 * np.maximum(led_img - 200.0, 0)
            box = np.stack([bleed, bleed, red], axis=-1) * np.float32(self.gain)
            if self.blur_sigma > 0:
                box = cv2.GaussianBlur(box, (0, 0), self.blur_sigma)
            img[y0:y1, x0:x1] = self._finish(box)
        if self.mirrored:
            img = img[:, ::-1]
        return np.ascontiguousarray(img)

    def _finish(self, img: np.ndarray) -> np.ndarray:
        """Add per-frame sensor noise and quantise to uint8."""
        if self.noise_sigma > 0:
            img = img + self._rng.standard_normal(img.shape, dtype=np.float32) * np.float32(self.noise_sigma)
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)

    def _background(self, gradient: np.ndarray) -> np.ndarray:
        """A small bank of noisy full-frame backgrounds, cached per (ambient, gain, noise)."""
        key = (self.ambient, self.gain, self.noise_sigma, self.size)
        if self._bg_key != key:
            w, h = self.size
            flat = np.repeat(np.broadcast_to(gradient, (h, w))[:, :, None], 3, axis=2) * np.float32(self.gain)
            self._bg = np.stack([self._finish(flat) for _ in range(BACKGROUND_BANK)])
            self._bg_key = key
        return self._bg


_PEAKS: dict[float, float] = {}


def _single_glow_peak(radius_frac: float) -> float:
    """Blurred value one pitch away from a single lit disc of level 1, so glow_frac is per neighbour."""
    if radius_frac not in _PEAKS:
        side = 3 * PITCH
        yy, xx = np.mgrid[0:side, 0:side].astype(np.float32) - (side / 2 - 0.5)
        disc = ((xx * xx + yy * yy) <= (radius_frac * PITCH) ** 2).astype(np.float32)
        blurred = cv2.GaussianBlur(disc, (0, 0), 0.6 * PITCH)
        _PEAKS[radius_frac] = float(blurred[side // 2, side // 2 + PITCH]) or 1.0
    return _PEAKS[radius_frac]
