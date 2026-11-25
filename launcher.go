package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

func launch() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	base := filepath.Dir(executable)
	input := bufio.NewReaderSize(os.Stdin, 16)
	fmt.Println("Quanah Cube")
	fmt.Println("1 = Preview OCR only")
	fmt.Println("2 = Start automatic resets")
	fmt.Print("Choose 1 or 2, then press Enter: ")
	choice, err := input.ReadString('\n')
	if err != nil {
		return err
	}

	args := []string{filepath.Join(base, "quanah_cube.py")}
	switch strings.TrimSpace(choice) {
	case "1":
		args = append(args, "--preview")
	case "2":
	default:
		return nil
	}

	child := exec.Command(filepath.Join(base, "runtime", "python.exe"), args...)
	child.Dir = base
	child.Stdin, child.Stdout, child.Stderr = os.Stdin, os.Stdout, os.Stderr
	return child.Run()
}

func main() {
	kernel := syscall.NewLazyDLL("kernel32.dll")
	kernel.NewProc("SetConsoleOutputCP").Call(65001)
	if err := launch(); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	fmt.Print("\nPress Enter to close...")
	bufio.NewReader(os.Stdin).ReadString('\n')
}
