//go:build linux

package interfaces

import (
	"fmt"
	"syscall"
)

// StartNetlinkListener listens to Linux kernel rtnetlink messages.
// This registers sub-second hardware interface state changes (Wi-Fi, Ethernet up/down).
func (im *InterfaceMonitor) StartNetlinkListener() {
	im.wg.Add(1)
	go func() {
		defer im.wg.Done()

		// Open standard routing netlink socket
		fd, err := syscall.Socket(syscall.AF_NETLINK, syscall.SOCK_RAW, syscall.NETLINK_ROUTE)
		if err != nil {
			fmt.Printf("[Netlink] Failed to open netlink socket: %v\n", err)
			return
		}
		defer syscall.Close(fd)

		// Bind socket to RTMGRP_LINK (Link up/down events) and RTMGRP_IPV4_IFADDR (IP changes)
		addr := &syscall.SockaddrNetlink{
			Family: syscall.AF_NETLINK,
			Groups: syscall.RTMGRP_LINK | syscall.RTMGRP_IPV4_IFADDR,
		}

		if err := syscall.Bind(fd, addr); err != nil {
			fmt.Printf("[Netlink] Failed to bind netlink: %v\n", err)
			return
		}

		buf := make([]byte, 4096)
		fmt.Println("[Netlink] Linux rtnetlink interface state listener active.")

		for {
			// Check if we received a stop signal
			select {
			case <-im.stopChan:
				return
			default:
			}

			n, err := syscall.Read(fd, buf)
			if err != nil {
				// Handle dynamic read timeout or errors
				continue
			}

			msgs, err := syscall.ParseNetlinkMessage(buf[:n])
			if err != nil {
				continue
			}

			for _, m := range msgs {
				switch m.Header.Type {
				case syscall.RTM_NEWLINK, syscall.RTM_DELLINK, syscall.RTM_NEWADDR, syscall.RTM_DELADDR:
					// Dynamic hardware event caught! Force instant interface list scan.
					logMsg := fmt.Sprintf("[Netlink] Kernel routing change caught (Type: %d). Scanning links...", m.Header.Type)
					fmt.Println(logMsg)
					im.scanInterfaces()
				}
			}
		}
	}()
}
