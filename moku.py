# Moku board recognition (RT-DETR, ONNX) — Python port of kaya's
# packages/board-recognition/src/moku-*.ts (https://github.com/kaya-go/kaya).
# Model: https://huggingface.co/kaya-go/moku-v4 (AGPL-3.0)
#
# Detects stones as objects (not per-cell crops), so it copes with oblique photos.
# Works for any board size; the size must be given.

from itertools import combinations

import cv2
import numpy as np
import onnxruntime as ort

INPUT_SIZE = 640
CLASS_BLACK, CLASS_WHITE, CLASS_CORNER = 0, 1, 2
DEFAULT_THRESHOLD = 0.035   # kaya stone threshold (used for corner fitting)
STONE_THRESHOLD = 0.05      # final threshold on the score averaged over passes
CROP_MARGIN = 0.12          # second pass crop: board corners pushed out by 12%
ADAPTIVE_FACTOR = 0.5       # low-confidence images: threshold = factor * score level ...
MIN_ADAPTIVE_THRESHOLD = 0.025  # ... but never below this
CORNER_MIN_THRESHOLD = 0.005
LINE_GATE = 0.6             # quads keep >= 60% of the best quad's grid-line evidence


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


class MokuDetector:
    def __init__(self, model_path, threads=None):
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(model_path, opts, providers=['CPUExecutionProvider'])

    def _run(self, rgb):
        x = cv2.resize(rgb, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
        x = (x.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]  # no mean/std normalization
        return dict(zip([o.name for o in self.session.get_outputs()],
                        self.session.run(None, {'pixel_values': x})))

    @staticmethod
    def _stones(out, w, h, min_score, x0=0, y0=0, flip=False):
        """Stone detections as (cx, cy, class, score, box width) in pixels of the full image.
        flip: the outputs come from the horizontally mirrored image."""
        probs = _sigmoid(out['logits'][0])           # (300, 3)
        boxes = out['pred_boxes'][0]                  # (300, 4) cx, cy, w, h normalized
        cls, score = probs.argmax(1), probs.max(1)
        keep = (cls != CLASS_CORNER) & (score >= min_score)
        cx = (1 - boxes[:, 0] if flip else boxes[:, 0]) * w
        return [(x0 + cx[i], y0 + boxes[i, 1] * h, int(cls[i]), float(score[i]), boxes[i, 2] * w)
                for i in np.where(keep)[0]]

    def detect(self, rgb, board_size=19, threshold=STONE_THRESHOLD, extra_corners=None, second_pass=True):
        """rgb: HxWx3 uint8. Returns (matrix, info); matrix row 0 = top, 0/1/2 = empty/black/white.
        extra_corners: optional board-corner candidates (pixels) from another detector.
        second_pass: run the model again on a crop around the board (stones get more pixels in the
        640x640 input) and on its mirror image, and average the three passes — on moku-v3 real
        photos this lifts perfect boards from ~54% to ~86%."""
        h, w = rgb.shape[:2]
        out = self._run(rgb)
        probs, boxes = _sigmoid(out['logits'][0]), out['pred_boxes'][0]

        corners, detected = self._corners(out.get('corner_points'), probs, boxes, w, h)
        stones = self._stones(out, w, h, DEFAULT_THRESHOLD)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        refined = self._best_quad(gray, out.get('corner_points'), stones, board_size, w, h, corners, extra_corners)
        if refined is not None:
            corners = refined

        corners = _upright(corners)
        passes = [self._stones(out, w, h, threshold / 2)]
        if second_pass and detected:
            c = corners.mean(0)
            q = c + (corners - c) * (1 + CROP_MARGIN)
            x0, y0 = np.maximum(q.min(0).astype(int), 0)
            x1, y1 = np.minimum(q.max(0).astype(int), [w, h])
            if x1 - x0 > 50 and y1 - y0 > 50:
                crop = rgb[y0:y1, x0:x1]
                for flip in (False, True):
                    out2 = self._run(np.ascontiguousarray(crop[:, ::-1] if flip else crop))
                    passes.append(self._stones(out2, x1 - x0, y1 - y0, threshold / 2, x0, y0, flip))
        matrix = self._vote(passes, corners, board_size, threshold)
        return matrix, {'corners': corners.tolist(), 'corners_detected': detected,
                        'corners_refined': refined is not None, 'passes': len(passes)}

    # --- corner selection by grid fit (improvement over kaya's top-4 by score) ---

    @staticmethod
    def _grid_fit(quad, stones, n):
        """How well detected stones fit the n x n grid spanned by this quad:
        + stones inside the board, sitting on an intersection, about one cell wide
        - stones inside the board but between intersections or of the wrong size
        - (smaller) stones outside the board"""
        dst = np.array([[0, 0], [n - 1, 0], [n - 1, n - 1], [0, n - 1]], np.float32)
        H = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
        cx, cy, wt, bw = stones
        g = cv2.perspectiveTransform(np.stack([cx, cy], 1)[None], H)[0]
        gl = cv2.perspectiveTransform(np.stack([cx - bw / 2, cy], 1)[None], H)[0]
        gr = cv2.perspectiveTransform(np.stack([cx + bw / 2, cy], 1)[None], H)[0]
        size = np.hypot(*(gr - gl).T)                       # stone width in cells, ~1 on the right grid
        size_ok = np.exp(-(np.log(np.maximum(size, 1e-3) / 0.95) / 0.35) ** 2)
        inside = np.all((g > -0.5) & (g < n - 0.5), axis=1)
        close = np.exp(-(np.hypot(*(g - np.round(g)).T) / 0.25) ** 2)
        return float(np.sum(wt * np.where(inside, 2 * close * size_ok - 1, -0.3)))

    def _best_quad(self, gray, corner_points, stones, n, w, h, default, extra=None):
        """Choose the board corners among Kaya's quad and every quad of 4 candidates.
        1. Grid lines: rectify each quad and measure the n x n grid lines in the photo; quads with
           clearly less line evidence than the best one (walls, lamps, half boards) are dropped.
        2. Stones: among the remaining quads, the one whose grid best explains the detected stones.
        Stones alone get fooled by false detections off the board (faces, bowls); lines alone pick
        a slightly wrong quad when a true corner is missing from the candidates."""
        cands = [] if corner_points is None else \
            [np.array([p[0] * w, p[1] * h]) for p in corner_points[0] if p[2] >= CORNER_MIN_THRESHOLD]
        cands += [np.array(c, float) for c in (extra or [])]
        quads = [default] + [q for q in (_order(np.array([cands[i] for i in c])) for c in combinations(range(len(cands)), 4))
                             if _plausible_board(q, w, h)]
        lm = _line_map(gray)
        lines = np.array([_line_score(lm, q, n) for q in quads])
        best_line = lines.max()
        keep = [i for i in range(len(quads)) if lines[i] - 1 >= LINE_GATE * (best_line - 1)]
        if len(stones) < 8:
            i = int(np.argmax(lines))
            return None if i == 0 else quads[i]
        st = tuple(np.array([s[k] for s in stones], np.float32) for k in (0, 1, 3, 4))
        best, best_score = None, -np.inf
        for i in keep:
            q = quads[i]
            inside = sum(cv2.pointPolygonTest(q.astype(np.float32), (float(x), float(y)), False) >= 0
                         for x, y in zip(st[0], st[1]))
            if i > 0 and inside < 0.5 * len(stones):
                continue
            score = self._grid_fit(q, st, n)
            if score > best_score:
                best, best_score = i, score
        if best is None:
            best = int(np.argmax(lines))
        return None if best == 0 else quads[best]

    # --- corners -----------------------------------------------------------------

    def _corners(self, corner_points, probs, boxes, w, h):
        if corner_points is not None:   # moku-v4 corner head: (1, K, 3) x, y, score (sigmoid applied)
            cp = corner_points[0]
            cands = [(p[0] * w, p[1] * h, float(p[2])) for p in cp if p[2] >= CORNER_MIN_THRESHOLD]
        else:                           # older models: corner queries
            idx = np.where((probs.argmax(1) == CLASS_CORNER) & (probs.max(1) >= CORNER_MIN_THRESHOLD))[0]
            cands = [(boxes[i, 0] * w, boxes[i, 1] * h, float(probs[i].max())) for i in idx]

        # dedupe: drop candidates within 5% of the diagonal of a better one
        cands.sort(key=lambda c: -c[2])
        min_dist = np.hypot(w, h) * 0.05
        kept = []
        for c in cands:
            if all(np.hypot(c[0] - k[0], c[1] - k[1]) >= min_dist for k in kept):
                kept.append(c)

        if len(kept) < 2:
            return _inset(w, h), False
        pts = [np.array(k[:2], float) for k in kept]
        if len(pts) == 2:
            pts = _complete_from_two(pts[0], pts[1], w, h)
        elif len(pts) == 3:
            pts = _complete_from_three(pts)
        corners = _order(np.array(pts[:4]))

        xs, ys = corners[:, 0], corners[:, 1]
        if (xs.max() - xs.min()) * (ys.max() - ys.min()) < w * h * 0.02:
            return _inset(w, h), False
        d = [np.hypot(*(corners[i] - corners[j])) for i in range(4) for j in range(i + 1, 4)]
        if min(d) < np.hypot(w, h) * 0.05:
            return _inset(w, h), False
        return corners, True

    @staticmethod
    def _vote(passes, corners, n, threshold):
        """Snap every pass's stones to the grid (best score wins an intersection within a pass),
        average the per-class scores over passes, keep intersections scoring >= threshold."""
        dst = np.array([[0, 0], [n - 1, 0], [n - 1, n - 1], [0, n - 1]], np.float32)
        H = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
        votes = np.zeros((n, n, 2))
        for stones in passes:
            best = np.zeros((n, n, 2))
            if stones:
                pts = np.array([[s[0], s[1]] for s in stones], np.float32)
                g = np.floor(cv2.perspectiveTransform(pts[None], H)[0] + 0.5).astype(int)  # JS Math.round
                for i in np.argsort([-s[3] for s in stones]):
                    col, row = g[i]
                    if 0 <= row < n and 0 <= col < n and not best[row, col].any():
                        best[row, col, stones[i][2]] = stones[i][3]
            votes += best
        votes /= len(passes)
        # Scores run low on images unlike the training photos (screenshots, renders): there the
        # threshold follows the image's own score level (median of its stronger half of stones).
        v = np.sort(votes.max(2)[votes.max(2) > threshold / 2])[::-1]
        if len(v):
            level = float(np.median(v[:max(5, len(v) // 2)]))
            threshold = min(threshold, max(MIN_ADAPTIVE_THRESHOLD, ADAPTIVE_FACTOR * level))
        return np.where(votes.max(2) >= threshold, votes.argmax(2) + 1, 0)


def _inset(w, h, f=0.05):
    m = min(w, h) * f
    return np.array([[m, m], [w - 1 - m, m], [w - 1 - m, h - 1 - m], [m, h - 1 - m]], float)


def _order(pts):
    """TL, TR, BR, BL: sort by angle around centroid, start from min(x+y)."""
    c = pts.mean(0)
    s = sorted(pts, key=lambda p: np.arctan2(p[1] - c[1], p[0] - c[0]))
    i = int(np.argmin([p[0] + p[1] for p in s]))
    return np.array([s[(i + k) % 4] for k in range(4)])


def _complete_from_two(p1, p2, w, h):
    m, d = (p1 + p2) / 2, p2 - p1
    hd = d / 2
    quads = [
        [p1, m + [hd[1], -hd[0]], p2, m + [-hd[1], hd[0]]],                      # diagonal
        [p1, p2, p2 + [-d[1], d[0]], p1 + [-d[1], d[0]]],                         # adjacent, +90
        [p1, p2, p2 + [d[1], -d[0]], p1 + [d[1], -d[0]]],                         # adjacent, -90
    ]

    def score(q):
        inside = sum(min(p[0], w - p[0]) >= 0 and min(p[1], h - p[1]) >= 0 for p in q)
        return inside * 1e6 + sum(min(p[0], w - p[0]) + min(p[1], h - p[1]) for p in q)
    return max(quads, key=score)


def _complete_from_three(pts):
    best, best_s = pts, np.inf
    for k in range(3):
        a, b, c = pts[k], pts[(k + 1) % 3], pts[(k + 2) % 3]
        p4 = b + c - a
        s = abs(np.hypot(*(a - p4)) - np.hypot(*(b - c)))
        if s < best_s:
            best, best_s = [a, b, c, p4], s
    return best


def _plausible_board(q, w, h):
    """A board seen in perspective: convex, not tiny, no very sharp angle, opposite sides comparable."""
    cross = [np.cross(q[(i + 1) % 4] - q[i], q[(i + 2) % 4] - q[(i + 1) % 4]) for i in range(4)]
    if not (all(c > 0 for c in cross) or all(c < 0 for c in cross)):
        return False
    if cv2.contourArea(q.astype(np.float32)) < 0.05 * w * h:
        return False
    sides = [np.hypot(*(q[(i + 1) % 4] - q[i])) for i in range(4)]
    if max(sides[0], sides[2]) > 3 * min(sides[0], sides[2]) or max(sides[1], sides[3]) > 3 * min(sides[1], sides[3]):
        return False
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        if np.degrees(np.arccos(np.clip(a @ b / (np.hypot(*a) * np.hypot(*b)), -1, 1))) < 35:
            return False
    return True


def _line_map(gray):
    """Thin dark lines (board grid) highlighted; large dark blobs (black stones) suppressed."""
    k = max(5, int(round(min(gray.shape) / 150)) | 1)
    return cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))).astype(np.float32)


def _line_score(lm, quad, n, k=12):
    """Grid-line evidence for an n x n grid in quad (TL, TR, BR, BL): rectify the line map, then compare
    the response on the expected grid lines (+-1/6 cell) with the response between them. ~1 = no grid."""
    pad = k // 2
    e = pad + (n - 1) * k
    dst = np.array([[pad, pad], [e, pad], [e, e], [pad, e]], np.float32)
    H = cv2.getPerspectiveTransform(np.asarray(quad, np.float32), dst)
    inner = cv2.warpPerspective(lm, H, (e + pad, e + pad), flags=cv2.INTER_LINEAR)[pad:e + 1, pad:e + 1]
    lines = np.arange(n) * k
    mids = lines[:-1] + k // 2
    t = max(1, k // 6)
    ratios = []
    for prof in (inner.mean(axis=1), inner.mean(axis=0)):
        on = np.mean([prof[max(0, p - t):p + t + 1].max() for p in lines])
        ratios.append(on / (prof[mids].mean() + 1e-3))
    return float(min(ratios))


def _upright(q):
    """Rotate the TL, TR, BR, BL order so the top edge is the one farthest from the camera
    (highest in the photo): the matrix then reads like the photo."""
    return min((np.roll(q, -k, axis=0) for k in range(4)), key=lambda r: r[0][1] + r[1][1])

