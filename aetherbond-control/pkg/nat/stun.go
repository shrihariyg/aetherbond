package nat

import (
	"crypto/rand"
	"encoding/binary"
	"fmt"
	"net"
	"time"
)

const (
	stunMagicCookie = 0x2112A442
	stunServer      = "stun.l.google.com:19302"
)

// NatProfile captures discovered mapping attributes for a link.
type NatProfile struct {
	PublicIP   string `json:"public_ip"`
	PublicPort uint16 `json:"public_port"`
	NatType    string `json:"nat_type"`
}

// DiscoverNAT pings google's STUN server over a bound local IP to discover mapped IP/Port and NAT type.
func DiscoverNAT(localIP string) (*NatProfile, error) {
	// Create bound UDP address to force packet through the chosen interface
	localAddr, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:0", localIP))
	if err != nil {
		return nil, fmt.Errorf("failed to resolve local address: %v", err)
	}

	remoteAddr, err := net.ResolveUDPAddr("udp", stunServer)
	if err != nil {
		return nil, fmt.Errorf("failed to resolve STUN server: %v", err)
	}

	conn, err := net.DialUDP("udp", localAddr, remoteAddr)
	if err != nil {
		return nil, fmt.Errorf("failed to dial STUN: %v", err)
	}
	defer conn.Close()

	// Set deadline for STUN response to prevent blocking
	conn.SetDeadline(time.Now().Add(2500 * time.Millisecond))

	// Formulate standard STUN Binding Request (RFC 5389)
	// Header: 20 bytes total
	// - Type: 0x0001 (Binding Request) [2 bytes]
	// - Length: 0x0000 [2 bytes]
	// - Magic Cookie: 0x2112A442 [4 bytes]
	// - Transaction ID: 12 random bytes
	packet := make([]byte, 20)
	binary.BigEndian.PutUint16(packet[0:2], 0x0001) // Type
	binary.BigEndian.PutUint16(packet[2:4], 0x0000) // Length
	binary.BigEndian.PutUint32(packet[4:8], stunMagicCookie)

	// Transaction ID
	_, _ = rand.Read(packet[8:20])

	_, err = conn.Write(packet)
	if err != nil {
		return nil, fmt.Errorf("failed to write STUN query: %v", err)
	}

	// Read response
	buf := make([]byte, 512)
	n, err := conn.Read(buf)
	if err != nil {
		return nil, fmt.Errorf("failed to read STUN response: %v", err)
	}

	if n < 20 {
		return nil, fmt.Errorf("STUN packet too short: %d bytes", n)
	}

	// Verify Magic Cookie
	cookie := binary.BigEndian.Uint32(buf[4:8])
	if cookie != stunMagicCookie {
		return nil, fmt.Errorf("invalid STUN cookie in response: 0x%X", cookie)
	}

	// Parse attributes (starting at index 20)
	idx := 20
	length := int(binary.BigEndian.Uint16(buf[2:4]))
	end := 20 + length

	publicIP := ""
	var publicPort uint16 = 0

	for idx+4 <= n && idx < end {
		attrType := binary.BigEndian.Uint16(buf[idx : idx+2])
		attrLen := int(binary.BigEndian.Uint16(buf[idx+2 : idx+4]))
		idx += 4

		if idx+attrLen > n {
			break
		}

		// 0x0001 = MAPPED-ADDRESS, 0x0020 = XOR-MAPPED-ADDRESS
		if attrType == 0x0001 {
			// Mapped Address
			family := buf[idx+1]
			if family == 1 { // IPv4
				publicPort = binary.BigEndian.Uint16(buf[idx+2 : idx+4])
				publicIP = net.IP(buf[idx+4 : idx+8]).String()
			}
		} else if attrType == 0x0020 {
			// XOR Mapped Address
			family := buf[idx+1]
			if family == 1 { // IPv4
				// Port is XORed with high 16 bits of magic cookie
				xorPort := binary.BigEndian.Uint16(buf[idx+2 : idx+4])
				publicPort = xorPort ^ uint16(stunMagicCookie>>16)

				// IP is XORed with full 32-bit magic cookie
				xorIP := binary.BigEndian.Uint32(buf[idx+4 : idx+8])
				rawIP := xorIP ^ stunMagicCookie
				ipBytes := make([]byte, 4)
				binary.BigEndian.PutUint32(ipBytes, rawIP)
				publicIP = net.IP(ipBytes).String()
			}
		}

		// Align index to 4-byte boundaries (STUN attribute requirement)
		padding := (4 - (attrLen % 4)) % 4
		idx += attrLen + padding
	}

	if publicIP == "" {
		return nil, fmt.Errorf("no mapped address found in STUN response")
	}

	// Determine NAT behavior classification:
	// A simple heuristic for multi-path systems:
	// If the public IP matches the interface local IP, it's a Direct Route.
	// Otherwise, it's a NAT channel (Full Cone or Symmetric NAT).
	natType := "NAT (Symmetric/Cone)"
	if publicIP == localIP {
		natType = "Public Address (No NAT)"
	} else if localIP == "127.0.0.1" || localIP == "0.0.0.0" {
		natType = "Simulated Loopback NAT"
	}

	return &NatProfile{
		PublicIP:   publicIP,
		PublicPort: publicPort,
		NatType:    natType,
	}, nil
}
