import os
import sys


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
