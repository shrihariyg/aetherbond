//go:build !linux

package interfaces

import "fmt"

// StartNetlinkListener acts as a safe, cross-platform no-op fallback
// on non-Linux systems, relying strictly on our high-frequency polling sweeps.
func (im *InterfaceMonitor) StartNetlinkListener() {
	fmt.Println("[Netlink] rtnetlink kernel monitoring is Linux-specific. Falling back to high-frequency active interface polling.")
}
