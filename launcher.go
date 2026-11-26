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
	fmt.Println("1 = 右侧 CP 增加时停止")
	fmt.Println("2 = 命中目标词条时停止")
	fmt.Println("3 = 命中目标词条或右侧 CP 增加时停止")
	fmt.Print("请选择 1–3，然后按回车：")
	choice, err := input.ReadString('\n')
	if err != nil {
		return err
	}

	args := []string{filepath.Join(base, "quanah_cube.py")}
	switch strings.TrimSpace(choice) {
	case "1":
	case "2":
		args = append(args, "--potential")
	case "3":
		args = append(args, "--potential", "--also-cp")
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
