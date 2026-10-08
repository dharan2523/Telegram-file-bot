import importlib.util
import pathlib
import sys

module_path = pathlib.Path(__file__).resolve().parent / 'downloader.py'
spec = importlib.util.spec_from_file_location('downloader_local', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

download_file = module.download_file

r = download_file('https://httpbin.org/bytes/1024')
print('SMALL_FILE_OK', r.filename, r.size, r.path.exists())
r.path.unlink(missing_ok=True)
