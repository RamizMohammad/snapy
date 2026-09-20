# -*- coding: utf-8 -*-
"""Tiny stroke-icon set, drawn as inline SVG and tinted at render time.

Each entry is the inner markup of a 24x24 viewBox with no fill and no stroke
colour; svg_icon() supplies those, so one definition serves every colour and
size the UI needs. Shapes are deliberately plain geometry.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICONS = {
    "phone": '<rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="10.5" y1="18.5" x2="13.5" y2="18.5"/>',
    "camera": '<path d="M3 8.5a2 2 0 0 1 2-2h2.2l1.3-2h6.9l1.3 2H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.4"/>',
    "history": '<path d="M3.2 12a8.8 8.8 0 1 0 2.6-6.2"/><polyline points="3,3 3,8.4 8.4,8.4"/><polyline points="12,7.4 12,12 15.4,13.8"/>',
    "compare": '<polyline points="7.5,4.5 3.5,8.5 7.5,12.5"/><line x1="3.5" y1="8.5" x2="14" y2="8.5"/><polyline points="16.5,11.5 20.5,15.5 16.5,19.5"/><line x1="20.5" y1="15.5" x2="10" y2="15.5"/>',
    "restore": '<path d="M20.8 12a8.8 8.8 0 1 1-2.6-6.2"/><polyline points="21,3 21,8.4 15.6,8.4"/>',
    "checklist": '<polyline points="3,7 5.2,9.2 9,5.4"/><polyline points="3,16 5.2,18.2 9,14.4"/><line x1="12.5" y1="7.3" x2="21" y2="7.3"/><line x1="12.5" y1="16.3" x2="21" y2="16.3"/>',
    "settings": '<circle cx="12" cy="12" r="3.1"/><path d="M12 1.9l1.5 2.3 2.7-.6.5 2.7 2.6 1-1.2 2.5 1.8 2.1-2.2 1.7.5 2.7-2.8.2-1.2 2.5L12 21.5l-2.2 1.6-1.2-2.5-2.8-.2.5-2.7-2.2-1.7 1.8-2.1L4.7 9.3l2.6-1 .5-2.7 2.7.6z" transform="translate(0,-0.6) scale(1,0.96)"/>',
    "shield": '<path d="M12 2.8 4.5 6v6.1c0 4.5 3.1 7.7 7.5 9.1 4.4-1.4 7.5-4.6 7.5-9.1V6z"/>',
    "usb": '<circle cx="12" cy="20" r="1.6"/><line x1="12" y1="18.4" x2="12" y2="7"/><polyline points="8.6,10.4 12,7 15.4,10.4"/><rect x="6.4" y="12" width="3.2" height="3.2" rx="0.6"/><circle cx="16" cy="13.6" r="1.7"/>',
    "sliders": '<line x1="4" y1="8" x2="20" y2="8"/><line x1="4" y1="16" x2="20" y2="16"/><circle cx="9.5" cy="8" r="2.2"/><circle cx="15" cy="16" r="2.2"/>',
    "snapshot": '<rect x="3.2" y="5.2" width="17.6" height="13.6" rx="2.2"/><polyline points="7,14.5 10.2,11.3 12.6,13.7 16,10.3 19,13.3"/><circle cx="8.6" cy="9.1" r="1.3"/>',
    "check": '<polyline points="4.5,12.5 9.5,17.5 19.5,6.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><polyline points="8,12.2 10.8,15 16,9.4"/>',
    "circle": '<circle cx="12" cy="12" r="8.4"/>',
    "spinner": '<path d="M12 3.6a8.4 8.4 0 1 0 8.4 8.4"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
    "chevron-up": '<polyline points="6,15 12,9 18,15"/>',
    "console": '<rect x="2.8" y="4.4" width="18.4" height="15.2" rx="2"/><polyline points="7,10 9.8,12.8 7,15.6"/><line x1="12.4" y1="15.6" x2="17" y2="15.6"/>',
}


def svg_icon(name: str, color: str, size: int = 18, stroke: float = 1.7,
             ratio: int = 2) -> QPixmap:
    """Return a crisp, tinted pixmap of one icon."""
    body = ICONS.get(name, ICONS["check"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm
