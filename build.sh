#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
repo=$PWD
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
package="$build/Quanah-Cube"
mkdir -p "$package/runtime" "$package/ocr" dist
curl -fsSL https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip -o "$build/python.zip"
curl -fsSL https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe -o "$build/tesseract.exe"
(cd "$build" && echo 'c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9  tesseract.exe' | sha256sum --check)
python3 -m pip download --only-binary=:all: --platform win_amd64 --python-version 312 --implementation cp --abi cp312 -r requirements.txt -d "$build"
unzip -oq "$build/python.zip" -d "$package/runtime"
for wheel in "$build"/*.whl; do
    unzip -oq "$wheel" -d "$package/runtime"
done
printf '.\r\npython312.zip\r\n' > "$package/runtime/python312._pth"
# 7-Zip can return 1 for the NSIS container warning. Verify extracted files below.
status=0
7zz x -y "-o$package/ocr" "$build/tesseract.exe" 'tesseract.exe' '*.dll' 'tessdata/eng.traineddata' 'doc/LICENSE' || status=$?
test "$status" -le 1
test -s "$package/ocr/tesseract.exe"
test -s "$package/ocr/libtesseract-5.dll"
test -s "$package/ocr/tessdata/eng.traineddata"
test -s "$package/ocr/doc/LICENSE"
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o "$package/Quanah-Cube.exe" launcher.go
cp "$(go env GOROOT)/LICENSE" "$package/Go-LICENSE.txt"
cp quanah_cube.py launcher.go "$package/"
cp README.md "$package/README.txt"
(cd "$build" && zip -qr Quanah-Cube-Windows-x64.zip Quanah-Cube)
unzip -tq "$build/Quanah-Cube-Windows-x64.zip"
mv "$build/Quanah-Cube-Windows-x64.zip" "$repo/dist/"
