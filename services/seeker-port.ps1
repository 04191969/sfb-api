function Test-SeekerBusy {
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 47602)
    $probe.Server.ExclusiveAddressUse = $true
    try { $probe.Start(); return $false }
    catch [System.Net.Sockets.SocketException] {
        $cause=$_.Exception
        while ($cause.InnerException) {$cause=$cause.InnerException}
        if ($cause.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AddressAlreadyInUse) { return $true }
        throw
    }
    finally { $probe.Stop() }
}
