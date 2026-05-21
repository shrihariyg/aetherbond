package telemetry

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

type LogLevel int

const (
	DEBUG LogLevel = iota
	INFO
	WARNING
	ERROR
)

var levelStrings = map[LogLevel]string{
	DEBUG:   "DEBUG",
	INFO:    "INFO",
	WARNING: "WARN",
	ERROR:   "ERROR",
}

// Global logger instance
var globalLogger *Logger

type Logger struct {
	fileWriter io.WriteCloser
	minLevel   LogLevel
}

func InitLogger(logFilePath string, minLevel LogLevel) error {
	// Create log directory if it doesn't exist
	dir := filepath.Dir(logFilePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create log directory: %w", err)
	}

	file, err := os.OpenFile(logFilePath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return fmt.Errorf("failed to open log file: %w", err)
	}

	globalLogger = &Logger{
		fileWriter: file,
		minLevel:   minLevel,
	}

	globalLogger.Infof("--- AetherBond Diagnostics Logger Initialized ---")
	return nil
}

func GetLogger() *Logger {
	if globalLogger == nil {
		// Fallback to basic stdout-only logger if not initialized
		globalLogger = &Logger{
			fileWriter: nil,
			minLevel:   DEBUG,
		}
	}
	return globalLogger
}

func (l *Logger) Close() {
	if l.fileWriter != nil {
		l.Infof("--- AetherBond Diagnostics Logger Closed ---")
		l.fileWriter.Close()
	}
}

func (l *Logger) log(level LogLevel, format string, args ...interface{}) {
	if level < l.minLevel {
		return
	}

	now := time.Now().Format("2006-01-02T15:04:05.000Z07:00")
	lvlStr := levelStrings[level]
	msg := fmt.Sprintf(format, args...)
	
	// Console layout (colorized)
	var colorCode string
	switch level {
	case DEBUG:
		colorCode = "\033[36m" // Cyan
	case INFO:
		colorCode = "\033[32m" // Green
	case WARNING:
		colorCode = "\033[33m" // Yellow
	case ERROR:
		colorCode = "\033[31m" // Red
	}
	
	consoleMsg := fmt.Sprintf("%s[%s] [%s] %s\033[0m\n", colorCode, now, lvlStr, msg)
	fmt.Print(consoleMsg)

	// File layout (JSON/structured string)
	if l.fileWriter != nil {
		fileMsg := fmt.Sprintf("[%s] [%s] %s\n", now, lvlStr, msg)
		l.fileWriter.Write([]byte(fileMsg))
	}
}

func (l *Logger) Debugf(format string, args ...interface{}) {
	l.log(DEBUG, format, args...)
}

func (l *Logger) Infof(format string, args ...interface{}) {
	l.log(INFO, format, args...)
}

func (l *Logger) Warnf(format string, args ...interface{}) {
	l.log(WARNING, format, args...)
}

func (l *Logger) Errorf(format string, args ...interface{}) {
	l.log(ERROR, format, args...)
}

// Global convenience wrappers
func Debugf(format string, args ...interface{}) {
	GetLogger().Debugf(format, args...)
}

func Infof(format string, args ...interface{}) {
	GetLogger().Infof(format, args...)
}

func Warnf(format string, args ...interface{}) {
	GetLogger().Warnf(format, args...)
}

func Errorf(format string, args ...interface{}) {
	GetLogger().Errorf(format, args...)
}
