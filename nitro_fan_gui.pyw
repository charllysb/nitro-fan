"""
Nitro Fan - liga/desliga o modo turbo (Max Fan) do Acer Nitro sem o NitroSense,
e mostra temperatura/RPM de CPU e GPU com alerta de temperatura critica.

Fala direto com o firmware via WMI (classe AcerGamingFunction):
  - SetGamingFanBehavior  -> modo dos fans
  - GetGamingSysInfo      -> sensores (comando 0x0001 | id_sensor << 8)
Sem dependencias: so Python 3 (tkinter vem junto). Extensao .pyw = abre sem console.
"""
import base64
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque

# ---------------------------------------------------------------- config
VALORES = {"turbo": 0x820009, "auto": 0x410009}  # CPU+GPU: 10=turbo, 01=auto
ESTADO_ARQ = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "nitro_fan_estado.json")

TEMP_ALERTA = 80    # °C - amarelo
TEMP_CRITICA = 90   # °C - vermelho + alerta
INTERVALO = 2       # segundos entre leituras
HIST_MIN = 5        # minutos no grafico

COR_FUNDO = "#0f1012"
COR_CARD = "#1a1c20"
COR_TEXTO = "#e8e8e8"
COR_SUAVE = "#7a7f87"
COR_TURBO = "#e3242b"   # vermelho Nitro
COR_AUTO = "#3a3f47"
COR_OK = "#3ecf6e"
COR_WARN = "#f0a500"
COR_CPU = "#4ea8ff"    # linhas do grafico
COR_GPU = "#c77dff"

NO_WINDOW = 0x08000000

PS_SET = r"""
$ErrorActionPreference='Stop'
$c=Get-CimClass -Namespace root/WMI -ClassName AcerGamingFunction
$i=Get-CimInstance -Namespace root/WMI -ClassName AcerGamingFunction | Select-Object -First 1
$m=$c.CimClassMethods['SetGamingFanBehavior']
$p=($m.Parameters | Where-Object { $_.Qualifiers.Name -contains 'In' } | Select-Object -First 1).Name
if(-not $p){ $p=($m.Parameters | Select-Object -First 1).Name }
$r=Invoke-CimMethod -InputObject $i -MethodName SetGamingFanBehavior -Arguments @{ $p=[uint64]__VALOR__ }
$o=($r.PSObject.Properties | Where-Object { $_.Name -notin 'ReturnValue','PSComputerName' -and $_.Value -ne $null } | Select-Object -First 1).Value
if($o -ne $null){ Write-Output $o } else { Write-Output 'OK' }
"""

# Processo PowerShell persistente: le sensores e imprime 1 linha JSON a cada INTERVALO.
PS_MONITOR = r"""
$ErrorActionPreference='Stop'
try {
  $c=Get-CimClass -Namespace root/WMI -ClassName AcerGamingFunction
  $i=Get-CimInstance -Namespace root/WMI -ClassName AcerGamingFunction | Select-Object -First 1
  $m=$c.CimClassMethods | Where-Object { $_.Name -like 'Get*SysInfo*' } | Select-Object -First 1
  $par=$m.Parameters | Where-Object { $_.Qualifiers.Name -contains 'In' } | Select-Object -First 1
  if(-not $par){ $par=$m.Parameters | Select-Object -First 1 }
  $p=$par.Name; $tipo=[string]$par.CimType
} catch { $m=$null }

function Ler([int]$id){
  if(-not $m){ return -1 }
  try {
    $cmd = 1 -bor ($id -shl 8)
    if($tipo -eq 'UInt32'){ $arg=[uint32]$cmd } else { $arg=[uint64]$cmd }
    $r=Invoke-CimMethod -InputObject $i -MethodName $m.Name -Arguments @{ $p=$arg }
    $o=[uint64](($r.PSObject.Properties | Where-Object { $_.Name -notin 'ReturnValue','PSComputerName' -and $_.Value -ne $null } | Select-Object -First 1).Value)
    if(($o -band 0xFF) -ne 0){ return -1 }
    return [int](($o -shr 8) -band 0xFFFF)
  } catch { return -1 }
}

function TempAcpi {
  try {
    $z=Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
       Sort-Object CurrentTemperature -Descending | Select-Object -First 1
    return [int]($z.CurrentTemperature/10 - 273.15)
  } catch { return -1 }
}

while($true){
  $cpu=Ler 1; if($cpu -le 0 -or $cpu -gt 125){ $cpu=TempAcpi }
  $d=[ordered]@{ cpu=$cpu; gpu=(Ler 10); fcpu=(Ler 2); fgpu=(Ler 6) }
  [Console]::Out.WriteLine(($d | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
  Start-Sleep -Seconds __INTERVALO__
}
"""


# ---------------------------------------------------------------- sistema
def eh_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevar():
    """Reabre o script como Administrador (pythonw para nao abrir console)."""
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{os.path.abspath(__file__)}"', None, 1)
    sys.exit(0)


def ps_args(script):
    """-EncodedCommand evita qualquer problema de aspas/quebras de linha."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]


def aplicar_modo(modo):
    """Retorna (ok, mensagem)."""
    try:
        r = subprocess.run(ps_args(PS_SET.replace("__VALOR__", hex(VALORES[modo]))),
                           capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except Exception as e:
        return False, f"Falha ao chamar PowerShell: {e}"
    if r.returncode != 0:
        erro = (r.stderr or r.stdout).strip().splitlines()
        return False, erro[0][:120] if erro else "Erro desconhecido"
    saida = r.stdout.strip()
    if saida.isdigit() and int(saida) & 0xFF != 0:
        return False, f"Firmware recusou (status {int(saida) & 0xFF})"
    return True, "OK"


def ler_estado():
    padrao = {"modo": "auto", "auto_ao_fechar": True, "turbo_auto_critico": False}
    try:
        with open(ESTADO_ARQ, encoding="utf-8") as f:
            padrao.update(json.load(f))
    except Exception:
        pass
    return padrao


def salvar_estado(dados):
    try:
        with open(ESTADO_ARQ, "w", encoding="utf-8") as f:
            json.dump(dados, f)
    except Exception:
        pass


class Monitor:
    """Mantem um PowerShell vivo lendo sensores; entrega dicts via callback."""

    def __init__(self, callback):
        self.callback = callback
        self.proc = None
        threading.Thread(target=self._rodar, daemon=True).start()

    def _rodar(self):
        script = PS_MONITOR.replace("__INTERVALO__", str(INTERVALO))
        try:
            self.proc = subprocess.Popen(ps_args(script), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, bufsize=1, creationflags=NO_WINDOW)
            for linha in self.proc.stdout:
                linha = linha.strip()
                if linha.startswith("{"):
                    try:
                        self.callback(json.loads(linha))
                    except ValueError:
                        pass
        except Exception:
            self.callback(None)

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


# ---------------------------------------------------------------- interface
def cor_temp(t):
    if t is None or t <= 0:
        return COR_SUAVE
    if t >= TEMP_CRITICA:
        return COR_TURBO
    if t >= TEMP_ALERTA:
        return COR_WARN
    return COR_OK


class CardSensor:
    """Card com temperatura grande e RPM; a bolinha colorida liga o card a linha do grafico."""
    W, H = 150, 84

    def __init__(self, pai, titulo, cor_linha):
        self.titulo = titulo
        self.cor_linha = cor_linha
        self.temp = None
        self.rpm = None
        self.c = tk.Canvas(pai, width=self.W, height=self.H, bg=COR_CARD, highlightthickness=0)
        self.desenhar()

    def atualizar(self, temp, rpm):
        self.temp = temp if temp is not None and 0 < temp <= 125 else None
        self.rpm = rpm if rpm is not None and rpm >= 0 else None
        self.desenhar()

    def desenhar(self):
        c, W = self.c, self.W
        c.delete("all")
        cor = cor_temp(self.temp)
        c.create_oval(12, 10, 20, 18, fill=self.cor_linha, outline="")
        c.create_text(26, 14, text=self.titulo, anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9, "bold"))
        if self.temp is not None and self.temp >= TEMP_CRITICA:
            c.create_text(W - 10, 14, text="CRÍTICO", anchor="e", fill=COR_TURBO, font=("Segoe UI", 8, "bold"))
        txt = f"{self.temp}°" if self.temp is not None else "--"
        c.create_text(12, 44, text=txt, anchor="w", fill=cor, font=("Segoe UI", 24, "bold"))
        rpm = f"{self.rpm} RPM" if self.rpm is not None else "RPM --"
        c.create_text(12, 72, text=rpm, anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9))


class Grafico:
    """Temperatura de CPU e GPU nos ultimos HIST_MIN minutos; a escala acompanha os dados."""
    W, H = 308, 150
    ESQ, DIR, TOPO, BASE = 34, 10, 26, 22   # margens

    def __init__(self, pai):
        self.hist = deque(maxlen=HIST_MIN * 60 // INTERVALO)
        self.c = tk.Canvas(pai, width=self.W, height=self.H, bg=COR_CARD, highlightthickness=0)
        self.desenhar()

    def adicionar(self, cpu, gpu):
        self.hist.append((cpu, gpu))
        self.desenhar()

    def desenhar(self):
        c, W, H = self.c, self.W, self.H
        c.delete("all")
        c.create_text(12, 14, text="TEMPERATURA", anchor="w", fill=COR_SUAVE, font=("Segoe UI", 9, "bold"))
        x0, x1, y0, y1 = self.ESQ, W - self.DIR, self.TOPO, H - self.BASE

        vals = [t for par in self.hist for t in par if t is not None]
        if not vals:
            c.create_text(W / 2, (y0 + y1) / 2, text="Lendo sensores...", fill=COR_SUAVE, font=("Segoe UI", 9))
            return

        # escala: margem de 3 °C, multiplos de 5, pelo menos 15 °C de altura
        lo = (min(vals) - 3) // 5 * 5
        hi = -((-(max(vals) + 3)) // 5) * 5
        if hi - lo < 15:
            hi = lo + 15
        passo = 5 if hi - lo <= 30 else 10

        def y_de(t):
            return y1 - (t - lo) / (hi - lo) * (y1 - y0)

        for t in range(int(lo), int(hi) + 1, passo):
            y = y_de(t)
            c.create_line(x0, y, x1, y, fill="#24272c")
            c.create_text(x0 - 6, y, text=f"{t}°", anchor="e", fill=COR_SUAVE, font=("Segoe UI", 7))
        if lo < TEMP_CRITICA <= hi:
            y = y_de(TEMP_CRITICA)
            c.create_line(x0, y, x1, y, fill=COR_TURBO, dash=(3, 3))
        c.create_text(x0, y1 + 12, text=f"-{HIST_MIN} min", anchor="w", fill=COR_SUAVE, font=("Segoe UI", 7))
        c.create_text(x1, y1 + 12, text="agora", anchor="e", fill=COR_SUAVE, font=("Segoe UI", 7))

        n = self.hist.maxlen - 1
        desloc = n + 1 - len(self.hist)   # dados novos entram pela direita
        for i, cor in ((0, COR_CPU), (1, COR_GPU)):
            pts = []
            for k, par in enumerate(self.hist):
                if par[i] is not None:
                    pts.append((x0 + (x1 - x0) * (k + desloc) / n, y_de(par[i])))
            if len(pts) >= 2:
                c.create_line(*[v for p in pts for v in p], fill=cor, width=2)
            if pts:
                x, y = pts[-1]
                c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=cor, outline="")


class App:
    TAM = 190

    def __init__(self, root):
        self.root = root
        self.estado = ler_estado()
        self.modo = self.estado["modo"]
        self.ocupado = False
        self.angulo = 0.0
        self.velocidade = 0.0
        self.critico = False
        self.ultimo_alerta = 0.0
        self.turbo_foi_automatico = False

        root.title("Nitro Fan")
        root.configure(bg=COR_FUNDO)
        root.resizable(False, False)

        tk.Label(root, text="NITRO FAN", bg=COR_FUNDO, fg=COR_TEXTO,
                 font=("Segoe UI", 16, "bold")).pack(pady=(16, 0))
        tk.Label(root, text="clique na ventoinha ou aperte espaço", bg=COR_FUNDO,
                 fg=COR_SUAVE, font=("Segoe UI", 9)).pack()

        self.canvas = tk.Canvas(root, width=self.TAM, height=self.TAM, bg=COR_FUNDO,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(pady=10)
        self.canvas.bind("<Button-1>", lambda e: self.alternar())
        root.bind("<space>", lambda e: self.alternar())

        self.lbl_modo = tk.Label(root, text="", bg=COR_FUNDO, font=("Segoe UI", 20, "bold"))
        self.lbl_modo.pack()
        self.lbl_status = tk.Label(root, text="", bg=COR_FUNDO, fg=COR_SUAVE, font=("Segoe UI", 9))
        self.lbl_status.pack(pady=(2, 12))

        cards = tk.Frame(root, bg=COR_FUNDO)
        cards.pack(padx=18)
        self.card_cpu = CardSensor(cards, "CPU", COR_CPU)
        self.card_gpu = CardSensor(cards, "GPU", COR_GPU)
        self.card_cpu.c.pack(side="left", padx=(0, 8))
        self.card_gpu.c.pack(side="left")
        self.grafico = Grafico(root)
        self.grafico.c.pack(padx=18, pady=(8, 0))

        opts = tk.Frame(root, bg=COR_FUNDO)
        opts.pack(pady=(12, 16), padx=18, anchor="w")
        self.var_fechar = tk.BooleanVar(value=self.estado["auto_ao_fechar"])
        self.var_auto_crit = tk.BooleanVar(value=self.estado["turbo_auto_critico"])
        for texto, var in ((f"Ligar TURBO sozinho se passar de {TEMP_CRITICA}°C", self.var_auto_crit),
                           ("Voltar ao AUTO ao fechar", self.var_fechar)):
            tk.Checkbutton(opts, text=texto, variable=var, command=self.persistir,
                           bg=COR_FUNDO, fg=COR_SUAVE, selectcolor=COR_CARD,
                           activebackground=COR_FUNDO, activeforeground=COR_TEXTO,
                           font=("Segoe UI", 9), bd=0, highlightthickness=0).pack(anchor="w")

        root.protocol("WM_DELETE_WINDOW", self.fechar)
        self.atualizar_rotulos("Estado salvo da última vez (pode não refletir o hardware)")
        self.monitor = Monitor(lambda d: root.after(0, self.on_sensores, d))
        self.animar()

    # ---- modo dos fans
    def alternar(self, novo=None, origem="manual"):
        if self.ocupado:
            return
        novo = novo or ("auto" if self.modo == "turbo" else "turbo")
        self.ocupado = True
        self.lbl_status.config(text="Aplicando...", fg=COR_SUAVE)
        threading.Thread(target=self._aplicar_thread, args=(novo, origem), daemon=True).start()

    def _aplicar_thread(self, novo, origem):
        ok, msg = aplicar_modo(novo)
        self.root.after(0, self._resultado, novo, ok, msg, origem)

    def _resultado(self, novo, ok, msg, origem):
        self.ocupado = False
        if not ok:
            self.lbl_status.config(text=msg, fg=COR_WARN)
            return
        self.modo = novo
        self.turbo_foi_automatico = (origem == "critico" and novo == "turbo")
        self.persistir()
        if origem == "critico":
            self.atualizar_rotulos("Turbo ligado automaticamente — temperatura crítica")
        else:
            self.atualizar_rotulos("Turbo ligado — CPU e GPU no máximo" if novo == "turbo"
                                   else "Controle automático do firmware")

    def atualizar_rotulos(self, status):
        turbo = self.modo == "turbo"
        self.lbl_modo.config(text="TURBO" if turbo else "AUTO", fg=COR_TURBO if turbo else COR_TEXTO)
        self.lbl_status.config(text=status, fg=COR_SUAVE)

    # ---- sensores
    def on_sensores(self, d):
        if d is None:
            self.lbl_status.config(text="Sensores indisponíveis neste modelo", fg=COR_WARN)
            return
        self.card_cpu.atualizar(d.get("cpu"), d.get("fcpu"))
        self.card_gpu.atualizar(d.get("gpu"), d.get("fgpu"))
        self.grafico.adicionar(self.card_cpu.temp, self.card_gpu.temp)

        temps = [t for t in (self.card_cpu.temp, self.card_gpu.temp) if t is not None]
        maxima = max(temps) if temps else 0
        estava_critico = self.critico
        self.critico = maxima >= TEMP_CRITICA

        if self.critico:
            self.alertar(maxima, primeira_vez=not estava_critico)
        else:
            self.root.title("Nitro Fan")

    def alertar(self, temp, primeira_vez):
        self.root.title(f"⚠ {temp}°C — CRÍTICO")
        agora = time.time()
        if primeira_vez or agora - self.ultimo_alerta > 60:  # no maximo 1 aviso/min
            self.ultimo_alerta = agora
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.after(1500, lambda: self.root.attributes("-topmost", False))
                self.root.bell()
                ctypes.windll.user32.FlashWindow(ctypes.windll.user32.GetParent(self.root.winfo_id()), True)
            except Exception:
                pass
        if self.var_auto_crit.get() and self.modo != "turbo" and not self.ocupado:
            self.alternar("turbo", origem="critico")

    # ---- persistencia / saida
    def persistir(self):
        salvar_estado({"modo": self.modo, "auto_ao_fechar": self.var_fechar.get(),
                       "turbo_auto_critico": self.var_auto_crit.get()})

    def fechar(self):
        self.monitor.parar()
        if self.var_fechar.get() and self.modo == "turbo":
            self.lbl_status.config(text="Voltando ao AUTO...")
            self.root.update()
            ok, _ = aplicar_modo("auto")
            if ok:
                self.modo = "auto"
        self.persistir()
        self.root.destroy()

    # ---- ventoinha animada
    def animar(self):
        alvo = 18.0 if self.modo == "turbo" else 2.5
        self.velocidade += (alvo - self.velocidade) * 0.06
        self.angulo = (self.angulo + self.velocidade) % 360
        self.desenhar_fan()
        self.root.after(16, self.animar)

    def desenhar_fan(self):
        c, t = self.canvas, self.TAM
        cx = cy = t / 2
        turbo = self.modo == "turbo"
        cor = COR_TURBO if turbo else COR_AUTO
        c.delete("all")

        if turbo or self.critico:
            for i, a in enumerate(("#2a0d0e", "#3d1012", "#55151a")):
                r = t / 2 - 2 - i * 3
                c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=a, width=3)

        r_anel = t / 2 - 14
        c.create_oval(cx - r_anel, cy - r_anel, cx + r_anel, cy + r_anel,
                      fill=COR_CARD, outline=cor, width=4)

        n_pas, r_in, r_out = 7, 16, r_anel - 9
        for k in range(n_pas):
            base = math.radians(self.angulo + k * 360 / n_pas)
            pts = []
            for s in range(9):
                f = s / 8
                r = r_in + (r_out - r_in) * f
                a = base + 0.55 * f
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            for s in range(8, -1, -1):
                f = s / 8
                r = r_in + (r_out - r_in) * f
                a = base + 0.55 * f + 0.38 * (1 - 0.4 * f)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            c.create_polygon(pts, fill=cor if turbo else "#5a606a", outline="", smooth=True)

        c.create_oval(cx - 18, cy - 18, cx + 18, cy + 18, fill=COR_FUNDO, outline=cor, width=2)
        c.create_text(cx, cy, text="N", fill=cor if turbo else COR_SUAVE, font=("Segoe UI", 11, "bold"))


def main():
    if os.name != "nt":
        print("Isso so funciona no Windows.")
        return
    if not eh_admin():
        elevar()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
