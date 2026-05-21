package interfaces

import (
	"net"
	"sync"
	"time"

	"aetherbond-control/pkg/telemetry"
)

// LinkState represents the operational telemetry of a physical adapter link.
type LinkState struct {
	Name      string    `json:"name"`
	IP        string    `json:"ip"`
	IsOnline  bool      `json:"is_online"`
	CheckedAt time.Time `json:"checked_at"`
}

// InterfaceMonitor manages active monitoring loops for network interfaces.
type InterfaceMonitor struct {
	mu           sync.RWMutex
	links        map[string]*LinkState
	pollInterval time.Duration
	stopChan     chan struct{}
	wg           sync.WaitGroup
}

// NewInterfaceMonitor creates a new instance of InterfaceMonitor.
func NewInterfaceMonitor(pollInterval time.Duration) *InterfaceMonitor {
	return &InterfaceMonitor{
		links:        make(map[string]*LinkState),
		pollInterval: pollInterval,
		stopChan:     make(chan struct{}),
	}
}

// Start boots the background polling routine to monitor adapter states.
func (im *InterfaceMonitor) Start() {
	im.wg.Add(1)
	go func() {
		defer im.wg.Done()
		ticker := time.NewTicker(im.pollInterval)
		defer ticker.Stop()

		// Initial sweep
		im.scanInterfaces()

		for {
			select {
			case <-ticker.C:
				im.scanInterfaces()
			case <-im.stopChan:
				return
			}
		}
	}()
}

// Stop safely terminates the interface scanning loops.
func (im *InterfaceMonitor) Stop() {
	close(im.stopChan)
	im.wg.Wait()
}

// scanInterfaces queries the OS network interface structures to trace IP bindings.
func (im *InterfaceMonitor) scanInterfaces() {
	ifaces, err := net.Interfaces()
	if err != nil {
		telemetry.Errorf("[InterfaceMonitor] Error listing interfaces: %v", err)
		return
	}

	im.mu.Lock()
	defer im.mu.Unlock()

	// Track newly discovered or updated links
	activeNames := make(map[string]bool)

	for _, iface := range ifaces {
		// Ignore down loopback interfaces
		if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}

		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}

		for _, addr := range addrs {
			ipNet, ok := addr.(*net.IPNet)
			if !ok {
				continue
			}

			// Capture IPv4 addresses only for bonding tunnel endpoints
			ip4 := ipNet.IP.To4()
			if ip4 == nil {
				continue
			}

			activeNames[iface.Name] = true
			ipStr := ip4.String()

			if state, exists := im.links[iface.Name]; exists {
				state.IP = ipStr
				state.IsOnline = true
				state.CheckedAt = time.Now()
			} else {
				im.links[iface.Name] = &LinkState{
					Name:      iface.Name,
					IP:        ipStr,
					IsOnline:  true,
					CheckedAt: time.Now(),
				}
				telemetry.Infof("[InterfaceMonitor] Discovered active adapter: %s (%s)", iface.Name, ipStr)
			}
			break
		}
	}

	// Flag dropped interfaces
	for name, state := range im.links {
		if !activeNames[name] {
			if state.IsOnline {
				state.IsOnline = false
				state.CheckedAt = time.Now()
				telemetry.Warnf("[InterfaceMonitor] Adapter offline: %s", name)
			}
		}
	}
}

// GetLinks returns a copy of current active interfaces and their states.
func (im *InterfaceMonitor) GetLinks() []LinkState {
	im.mu.RLock()
	defer im.mu.RUnlock()

	var result []LinkState
	for _, link := range im.links {
		result = append(result, *link)
	}
	return result
}
