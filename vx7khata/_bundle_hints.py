"""Static imports that exist only so PyInstaller bundles modules ReportLab loads dynamically.

ReportLab's barcode package builds its widgets with exec("from reportlab.graphics.barcode.code128 import ...")
at import time. PyInstaller cannot see inside those strings, so each module is imported explicitly here.
Importing this module has no other effect.
"""
# flake8: noqa
import reportlab.graphics.barcode.code128
import reportlab.graphics.barcode.code39
import reportlab.graphics.barcode.code93
import reportlab.graphics.barcode.common
import reportlab.graphics.barcode.dmtx
import reportlab.graphics.barcode.eanbc
import reportlab.graphics.barcode.ecc200datamatrix
import reportlab.graphics.barcode.fourstate
import reportlab.graphics.barcode.lto
import reportlab.graphics.barcode.qr
import reportlab.graphics.barcode.qrencoder
import reportlab.graphics.barcode.usps
import reportlab.graphics.barcode.usps4s
import reportlab.graphics.barcode.widgets
