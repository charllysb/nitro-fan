# nitro-fan.ps1 - Liga/desliga o modo turbo (Max Fan) do Acer Nitro sem o NitroSense
# Uso:  nitro-fan.ps1 turbo   |   nitro-fan.ps1 auto   |   nitro-fan.ps1 info
# Chama direto o metodo WMI SetGamingFanBehavior do firmware (o mesmo que o NitroSense usa).

param(
    [ValidateSet('turbo', 'auto', 'info')]
    [string]$Modo = 'turbo'
)

# --- Auto-elevacao para Administrador ---
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $Modo"
    exit
}

# Valores: bits 0-15 = quais fans (0x0009 = CPU + GPU); bits 16-23 = modo de cada fan (01 = auto, 10 = turbo)
$valores = @{
    turbo = [uint64]0x820009
    auto  = [uint64]0x410009
}

$ns = 'root/WMI'
$classe = 'AcerGamingFunction'

try {
    $cls  = Get-CimClass -Namespace $ns -ClassName $classe -ErrorAction Stop
    $inst = Get-CimInstance -Namespace $ns -ClassName $classe -ErrorAction Stop | Select-Object -First 1
} catch {
    Write-Host "ERRO: a classe $classe nao existe neste notebook (firmware sem a interface gaming da Acer)." -ForegroundColor Red
    Write-Host "Nesse caso a saida e o NoteBook FanControl ou o AcerHelper."
    Read-Host "Enter para sair"; exit 1
}

if ($Modo -eq 'info') {
    Write-Host "Metodos disponiveis em ${classe}:" -ForegroundColor Cyan
    $cls.CimClassMethods | ForEach-Object {
        $params = ($_.Parameters | ForEach-Object { "$($_.Name):$($_.CimType)" }) -join ', '
        Write-Host "  $($_.Name)($params)"
    }
    Read-Host "Enter para sair"; exit 0
}

$metodo = $cls.CimClassMethods['SetGamingFanBehavior']
if (-not $metodo) {
    Write-Host "ERRO: metodo SetGamingFanBehavior nao encontrado. Rode com 'info' e me manda a lista." -ForegroundColor Red
    Read-Host "Enter para sair"; exit 1
}

# Descobre o nome do parametro de entrada (varia entre firmwares)
$paramIn = ($metodo.Parameters | Where-Object { $_.Qualifiers.Name -contains 'In' } | Select-Object -First 1).Name
if (-not $paramIn) { $paramIn = ($metodo.Parameters | Select-Object -First 1).Name }

Write-Host "Aplicando modo '$Modo' (0x{0:X}) via $paramIn..." -f $valores[$Modo] -ForegroundColor Cyan
$r = Invoke-CimMethod -InputObject $inst -MethodName SetGamingFanBehavior -Arguments @{ $paramIn = $valores[$Modo] }

$saida = $r.PSObject.Properties | Where-Object { $_.Name -notin 'ReturnValue','PSComputerName' -and $_.Value -ne $null } | Select-Object -First 1
if ($saida) {
    $status = [uint64]$saida.Value -band 0xFF
    if ($status -eq 0) { Write-Host "OK! Fans em modo '$Modo'." -ForegroundColor Green }
    else { Write-Host "Firmware respondeu com status $status (diferente de 0 = recusou)." -ForegroundColor Yellow }
} else {
    Write-Host "Comando enviado. Escuta o cooler pra confirmar." -ForegroundColor Green
}
Read-Host "Enter para sair"
