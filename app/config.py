from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"

MAX_FILE_SIZE_MB = 1.8
MAX_FILE_SIZE_BYTES = int(MAX_FILE_SIZE_MB * 1024 * 1024)

PDF_FOLDER_NAME = "PDF"
CONTRACTOR_FOLDER_NAME = "R&ДоговорПодряда"
SUBCONTRACTOR_FOLDER_NAME = "D&ДоговораСубподряда"
