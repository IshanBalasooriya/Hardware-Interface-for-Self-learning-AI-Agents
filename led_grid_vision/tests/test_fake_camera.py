import numpy as np
import cv2

from vision import patterns as P
from vision.camera import open_camera
from vision.fake_camera import DEFAULT_QUAD, FakeCamera, N, PITCH


def _quad_mask(cam):
    w, h = cam.size
    mask = np.zeros((h, w), np.uint8)
    q = np.int32(cam.quad)
    cv2.fillConvexPoly(mask, q[[0, 1, 3, 2]], 1)
    return mask.astype(bool)


def _cell_point(quad, rc_grid):
    """Image point of a grid-space position (row, col) in cell units, for rotation 0."""
    side = N * PITCH
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [side, 0], [0, side], [side, side]]),
                                    np.float32(quad))
    r, c = rc_grid
    p = cv2.perspectiveTransform(np.float32([[[(c + 0.5) * PITCH, (r + 0.5) * PITCH]]]), m)[0, 0]
    return int(round(p[0])), int(round(p[1]))


def test_frame_shape_and_dtype():
    cam = FakeCamera()
    frame = cam.grab(1)[0]
    assert frame.shape == (720, 1280, 3) and frame.dtype == np.uint8


def test_open_camera_fake_and_context():
    with open_camera("fake") as cam:
        assert isinstance(cam, FakeCamera)
        assert cam.info()["source"] == "fake"


def test_all_on_brighter_in_quad():
    cam = FakeCamera()
    mask = _quad_mask(cam)
    cam.set_rows(P.all_off())
    off = cam.grab(1)[0][..., 2][mask].mean()
    cam.set_rows(P.all_on())
    on = cam.grab(1)[0][..., 2][mask].mean()
    assert on > off + 30


def test_red_dominant():
    cam = FakeCamera(rows=P.all_on())
    f = cam.grab(1)[0][_quad_mask(cam)].astype(float)
    assert f[:, 2].mean() > 2 * f[:, 1].mean()


def test_deterministic_for_seed():
    a = FakeCamera(rows=P.checker(0), seed=3, banding=0.3).grab(3)
    b = FakeCamera(rows=P.checker(0), seed=3, banding=0.3).grab(3)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    c = FakeCamera(rows=P.checker(0), seed=4, banding=0.3).grab(3)
    assert not all(np.array_equal(x, y) for x, y in zip(a, c))


def test_blocked_is_flat():
    f = FakeCamera(rows=P.all_on(), blocked=True).grab(1)[0]
    assert f.max() == f.min() and f.max() < 20


def test_occluded_cell_dark():
    occ = (3, 4)
    pt = _cell_point(DEFAULT_QUAD, occ)
    lit = FakeCamera(rows=P.all_on(), noise_sigma=0).grab(1)[0]
    dark = FakeCamera(rows=P.all_on(), noise_sigma=0, occluded={occ}).grab(1)[0]
    x, y = pt
    assert lit[y, x, 2] > 150
    assert dark[y, x, 2] < 60


def _brightest(frame):
    blur = cv2.GaussianBlur(frame[..., 2].astype(np.float32), (0, 0), 2)
    y, x = np.unravel_index(np.argmax(blur), blur.shape)
    return int(x), int(y)


def test_rotation_moves_corner_tl():
    pts = {}
    for rot in (0, 90, 180, 270):
        cam = FakeCamera(rows=P.corner("tl"), rotation=rot, noise_sigma=0)
        pts[rot] = _brightest(cam.grab(1)[0])
    tl, tr, bl, br = (_cell_point(DEFAULT_QUAD, rc) for rc in ((0, 0), (0, 7), (7, 0), (7, 7)))
    near = lambda a, b: abs(a[0] - b[0]) <= 3 and abs(a[1] - b[1]) <= 3
    assert near(pts[0], tl)
    assert near(pts[90], tr)
    assert near(pts[180], br)
    assert near(pts[270], bl)


def test_mirror_flips_horizontally():
    a = FakeCamera(rows=P.corner("tl"), noise_sigma=0).grab(1)[0]
    b = FakeCamera(rows=P.corner("tl"), noise_sigma=0, mirrored=True).grab(1)[0]
    assert np.array_equal(a[:, ::-1], b)


def test_gain_scales_brightness():
    cam = FakeCamera(rows=P.all_on(), noise_sigma=0)
    mask = _quad_mask(cam)
    base = cam.grab(1)[0][..., 2][mask].mean()
    cam.gain = 0.5
    assert abs(cam.grab(1)[0][..., 2][mask].mean() - 0.5 * base) < 0.05 * base


def test_banding_frames_differ_but_mean_close():
    mask = _quad_mask(FakeCamera())
    plain = FakeCamera(rows=P.all_on(), noise_sigma=0).grab(1)[0][..., 2][mask].astype(float)
    frames = FakeCamera(rows=P.all_on(), noise_sigma=0, banding=0.3, seed=1).grab(40)
    means = [f[..., 2][mask].mean() for f in frames]
    assert max(means) - min(means) > 5
    avg = np.mean([f[..., 2][mask].astype(float) for f in frames], axis=0)
    assert abs(avg.mean() - plain.mean()) < 0.05 * plain.mean()
