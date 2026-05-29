import sys
import os

src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from oraplan import run_oraplan

def main():
    run_oraplan(sys.argv)

if __name__ == '__main__':
    main()
