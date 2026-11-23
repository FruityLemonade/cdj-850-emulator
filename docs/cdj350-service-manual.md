# CDJ-350 service manual: agent reference

Digest of the Pioneer CDJ-350 service manual (order no. **RRV4069**, 2010,
97 pages; types SYXJ8, CUXJ, FLXJ, KXJ5, AXJ5), read from the ManualsLib
copy (manual 1108648). Its viewer page numbers equal the manual's printed
page numbers, so **p.N below is both**. It is the CDJ-350 counterpart of
`docs/cdj850-service-manual.md`; firmware-side findings are in
`docs/cdj350.md` and `scripts/ghidra/cdj350-annotations.json`.

How reliable this is: ManualsLib serves the pages as flattened text. Labels and
net names are dependable; pairings across table columns (a part number with
its designator, a pin number with its net) were rebuilt from text order on
the schematic and parts-list pages. Those are marked *(order)*. Where two
pages disagree, both readings are given. Things the readable text does not
settle are marked **open**.

---

## 1. Hardware at a glance

### 1.1 Devices (§6.2 table, p.31)

| Device | Function | Part | Ref | Assy |
|---|---|---|---|---|
| MAIN CPU | system control | R5S72630P200FP (SH7263, core 200 MHz) | IC103 | MAIN |
| FLASH | firmware memory for MAIN CPU | DYW1792 (= S29GL032N90TFI030, "FLASH ROM (32M)", p.58) | IC101 | MAIN |
| SDRAM | work memory for MAIN CPU | K4S561632J-UC75 ("SDRAM (256M)", 66 MHz; substitute H57V2562GTR-75C-K, p.58) | IC102 | MAIN |
| Audio DSP | DSP | DSPC56371AF180 | IC301 | MAIN |
| Servo DSP | DSP | TC94A15FG | IC201 | MAIN |
| Servo driver | disc drive control | BD7956FS | IC204 | MAIN |
| Panel microcomputer | button input, LED and FL control | IC501 DYW1793 (M3030RFCPFP-K, M16C); service part PEG740A8-K (p.15, p.31) | IC501 | PNLB |

Memory sizes on the block diagram (p.16) read "32MB" and "256MB"; the parts
are 32 Mbit and 256 Mbit (4 MB flash, 32 MB SDRAM), as on the CDJ-850.

### 1.2 Other MAIN-board ICs (p.94 parts list, *order*; functions from p.18 and p.64)

| Ref | Part | Role |
|---|---|---|
| IC104 | (not in list) | reset buffer, output MAIN_RST (p.58) |
| IC105 | TC74VHCU04FT | buffers SERVO16M / AUDIO16M → CLK_S_16M, CLK_A_16M |
| IC107 | TS3USB30RSW | USB D+/D- switch, select USB_ABSEL |
| IC203 | NJM2903M | "TRACK COUNT" (p.61) |
| IC205 | TC7W04FU | logic |
| IC206 | BA00BC0WFP | 5.6 V regulator |
| IC302 | PCM1742KE | audio DAC |
| IC303 | RNB4580F | output op-amp (Lch/Rch) |
| IC401 | BA33BC0WFP | 3.3 V (V+3R3) |
| IC402, IC408 | S-1170B33UC-OTS | 3.3 V: V+3R3A (DAC, DSP) / V+3R3S |
| IC403, IC404 | BD00KA5WFP | 1.25 V (V+1R25) / 1.2 V (V+1R2) |
| IC405, IC406 | NJM2872BF05 | 5.0 V: V+5P/V+5M (pickup, servo) / V+5A (DAC, filter) |
| IC409 | BD9326EFJ | DC-DC, 5.6 V from V+7R6DCDC |
| IC410, IC411 | TC74VHCT08AFTS1, TC74VHC08FTS1 | AND gates on the panel-link lines (TSCS near IC411 pin 2, *order*) |

### 1.3 Crystals

| Ref | Frequency | Use |
|---|---|---|
| X101 | 33.00 MHz (DSS1193) | MAIN CPU clock (p.94); p.59's text pairs it with "to SERVO, CLK_S_16M", *order* |
| X102 | 48.00 MHz (ASS7099) | USB (USB_X1/X2) |
| X103 | 16.9344 MHz (VSS1084) | through IC105 to CLK_S_16M (servo) and CLK_A_16M (audio DSP) |
| X501 | 15.975 MHz (DSS1166) | panel CPU IC501 |

MAIN bus clock CLK66M is IC103 pin 50 (waveform 1, p.77).

### 1.4 Board assemblies (p.13, p.94)

| Assembly | Part no. |
|---|---|
| PANL ASSY | DWM2380 (NSP) |
| PNLB ASSY | DWX3106: panel CPU, FL V501, keys, USB-A JA505, IC504/IC505 |
| JACK ASSY | DWX3107: audio out JA701/JA702, USB-B JA703, CN701 |
| SLMB ASSY | DWX3108: slot-in switches S751/S752, CN751 |
| TCHB ASSY | DWX3109: jog touch (IC801 GP1S094HCZ0F, Q801) |
| JOGB ASSY | DWX3124: jog encoder PC851 SEDS-7573 |
| MACN ASSY | DWM2391 (NSP) |
| MAIN ASSY | DWX3105 |
| CNCT ASSY | DWX3111 |
| SMPS ASSY | DWR1482 (SYXJ8/FLXJ/KXJ5/AXJ5), DWR1481 (CUXJ) |
| SLOTIN MECHA ASSY | DXA2206 (NSP); TM ASSY-S (VTM091) DXX2616 |

Compared with the CDJ-850 the MAIN board carries the same CPU, DSP, memory,
servo, DAC and USB switch; the panel CPU, the panel link and the display
differ, and the USB-A port and its VBUS switch sit on the panel board.

---

## 2. Specifications (§2.1, p.7)

| Item | Value |
|---|---|
| Power | AC 220-240 V (SYXJ8, AXJ5), 110-240 V (FLXJ), 120 V 60 Hz (CUXJ), 220 V 60 Hz (KXJ5) |
| Consumption | 16 W, standby 0.3 W |
| Weight / size | 2.3 kg; 220 × 107 × 288.5 mm |
| Audio out | RCA, 2.0 Vrms (1 kHz); 4 Hz-20 kHz; S/N 115 dB; THD 0.006 % |
| USB downstream | Type A, 5 V / 500 mA max |
| USB upstream | Type B |
| Control out | mini-jack (CONT1/CONT2) |

Laser 790 nm, 4 mW CW, Class 1 (p.2). The laser interlock reads S752 (loading
complete); LPS1 is high while the mechanism is not clamped; test mode
disables the interlock (p.2).

---

## 3. MAIN ↔ panel link

### 3.1 CN402 (MAIN, RKN1068-A) = CN502 (PNLB, VKN1287-A), 27 pins (p.15, p.17, p.65, p.68)

| Pin | Net | Pin | Net |
|---|---|---|---|
| 1 | USBA_D_P | 15 | TSI |
| 2 | USBA_D_N | 16 | PNRDY |
| 3 | GNDD | 17 | TSO |
| 4 | USB_CDETFLG (printed USB_CODETFLG on p.24/p.68) | 18 | TSCS |
| 5 | GNDD | 19 | GNDD |
| 6 | USB_VBUSON | 20 | TSCK |
| 7 | GNDD | 21 | GNDD |
| 8 | POWER_ON | 22 | V+5R6 |
| 9 | GNDD | 23 | V+5R6 |
| 10 | VDET | 24 | V+32 |
| 11 | GNDD | 25 | GNDD |
| 12 | MUTE_PNL | 26 | V+5EV |
| 13 | GNDD | 27 | GNDD |
| 14 | MAIN_RST | | |

Series resistors on the MAIN side are 33 Ω with 100-330 pF to ground (p.65);
on the panel side 0-220 Ω (TSCK R650 0 Ω, TSO R600 220 Ω, PNRDY R508 220 Ω,
TSI R505 100 Ω, *order*, p.68). The panel CPU drives TSCK (IC501 pin 37, p.27).

The CDJ-850 link has TSCK/TSO/TSI plus PNL_XBUSY and MAIN_XBUSY; the CDJ-350
names TSCS and PNRDY instead. The firmware still runs SCIF1 with IRQ5 and the
same ISRs (`docs/cdj350.md`), so the emulator treats them as the same lines.
Which MAIN pins carry TSCS and PNRDY is **open** (the IC103 pin table was not
in the readable pages).

### 3.2 Link waveforms (§10.11, p.77; MAIN)

| # | Point | V/div | Time/div |
|---|---|---|---|
| 7 | foot of R162 (TSCS) | 1.0 V | 500 µs |
| 8 | foot of R191 (TSCK) | 1.0 V | 1 µs |
| 9 | foot of R163 (TSI) | 1.0 V | 50 µs |
| 10 | foot of R193 (TSO) | 1.0 V | 50 µs |

No period, burst length or handshake timing is printed; PNRDY has no
waveform.

---

## 4. Power-on sequence (§5.1, p.19)

1. AC in: the panel CPU is powered from EV+5 (V+5EV); reset from IC502
   (BU4242G) to IC501 pin 12.
2. Panel CPU in STANDBY: FL dark, STANDBY/ON LED red.
3. POWER ON: "POWER ON" on the FL, LED green; panel CPU raises POWER_ON
   (IC501 pin 4), then releases the MAIN reset (IC501 pin 40 → IC103 pin 59).
4. MAIN downloads the program to the audio DSP (IC301), then releases
   IC103 pin 158 → IC301 pin 47 (DSP reset) and IC103 pin 69 → IC201 pin 90
   (servo reset).
5. MAIN ↔ panel ↔ audio communication starts. **Failure: E-8709 on the FL.**
6. Drive bring-up: inside switch check, LD on, tracking/focus offset
   adjustment, spindle kick, focus sweep, focus on, focus OK check, spindle
   acceleration (100 ms), CLV on, wait 300 ms, tracking balance, tracking
   close, GFS (RF lock) check, TOC read, first-track search, play with muting
   off. If the focus check fails once the S-curve is re-measured with RF gain
   raised (≤ 0.88 Vp-p → retry with OEIC and RG gains raised); after three
   failures "EJECT" shows.

This matches the CDJ-850 sequence and the emulator's boot: "EJECT", then
"NO DISC" with no disc.

---

## 5. Error codes (§6.3 [4], p.34; §5.2 [1]/[8], p.21, p.26)

The time display shows "E-XXXX". Code-to-meaning pairing is from scrambled
columns *(order)*.

| Code | Name | Medium | Meaning |
|---|---|---|---|
| E-6002 | DSP PROGRAM | - | program cannot be written to the audio DSP (p.26 heads the same code "COMMUNICATION ERROR"; checks: V+3R3A, V+1R25, IC301 running) |
| E-7024 | - | - | firmware update failed; repeat the update (recovery, USB only) |
| E-7201 | TOC READ ERROR | CD | TOC cannot be read |
| E-8301 | PLAYER ERROR | CD | focus servo cannot be closed |
| E-8302 | PLAYER ERROR | CD | pickup cannot return to the inner track / address not found |
| E-8303 | buffer write error | CD | address unreadable, buffer write failed |
| E-8304 | decoding error | CD | MP3/AAC decode error |
| E-8305 | format error | CD | unsupported MP3/AAC/WAV/AIFF format |
| E-8306 | no music file | USB | track erased since it was registered |
| E-8307 | USB writing error | USB | write to the USB device failed |
| E-8709 | COMMUNICATION ERROR | - | panel and MAIN microcomputers cannot communicate |
| E-9101 | LOADING TIMEOUT | CD | mechanical timeout loading or ejecting |

E-8709 checks (p.21): TSCS/TSCK/TSI waveforms, V+3R3/V+1R2, PNRDY level, TSO;
PNRDY stuck low or TSO stuck means IC103 may be defective.

POWER switch flashing red = IJO DETECT, a supply fault (p.21): VDET low means
a voltage error (V+5IJO or V+7R6M drop, VFDP32 drop/rise, V+1R2 or V+1R25
rise); VDET high points at the SMPS.

---

## 6. Service modes (§6, p.31-36)

### 6.1 Entry (POWER ON while holding, p.32)

| Mode | Keys | Notes |
|---|---|---|
| Version and error history | RELOOP for 10 s or more (not with the TEMPO slider at either end) | BPM LOCK cycles MAIN, DISPLAY and DSP versions; VINYL MODE switches version / error history; DIVIDE LEFT/RIGHT page the error log |
| Service mode: panel test, JOG load | BACK + TEMPO RANGE | FL shows "SERVICE MODE"; TEMPO slider to + (bottom) enters the JOG load measurement; POWER OFF to quit |
| CD drive diagnosis | TIME + CD SELECT | POWER OFF to quit |
| Firmware update from USB | SOURCE SELECT USB + RELOOP | until "DOWNLOAD" shows |
| Firmware update from CD-ROM | SOURCE SELECT DISC + RELOOP | |
| Factory reset | SOURCE SELECT USB + BACK | resets the §8.3 items |

The CDJ-850 enters its panel test with BACK + TEMPO as well, and its update
with USB + RELOOP.

### 6.2 Panel test (mode 2, p.33)

Each key, the jog, the slider and the display respond; the indication lights
while a key is held. Names as printed:

- **Keys:** PLAY/PAUSE, CUE, IN/CUE/HOT LOOP, OUT/OUT ADJUST, RELOOP/EXIT,
  TRACK REV, TRACK FWD, SEARCH REV, SEARCH FWD, JOG MODE, TEMPO, MASTER TEMPO,
  TIME MODE/AUTO CUE, DISPLAY/UTILITY, BACK, EJECT, BEAT LOOP/LOOP DIVIDE
  (LEFT), BEAT LOOP/LOOP DIVIDE (RIGHT), BEAT LOOP/LOOP DIVIDE, JOG TOUCH,
  TEMPO SLIDER, JOG FWD, JOG REV, USB_STOP, USB, CD, PC, ROTARY SELECTOR
  push / turn right / turn left, PLAYLIST, BPM LOCK.
- **FL readings per key:** PLAY, CUE, IN, OUT, RELOOP, TRACK |<<, TRACK >>|,
  REV <<, FWD >>, JOG MODE, TEMPO, MASTER TEMPO, TIME/ACUE, DISPLAY, EJECT,
  DIVIDE <-, DIVIDE ->, JOG TOUCH SW, JOG >, JOG <, USB STOP, USB, CD, PC,
  PUSH, ADD PLAYLIST, BPM ADJUST, then "All FL lights up".
- **LEDs:** PLAY/PAUSE, CUE, IN/CUE/HOT LOOP, OUT/OUT ADJUST, RELOOP/EXIT,
  VINYL, MASTER TEMPO, EJECT, BEAT LOOP/LOOP DIVIDE, USB_STOP, USB, CD, PC,
  then all LEDs.

### 6.3 Drive check (mode 3, p.35-36)

Player-operation mode: Servo All Off, Play/Pause (traces at 4× without
sound), Track Search, Error Rate Count (about 10 s; below 3.00E-3 is OK, e.g.
"3.56E-4 O.K."), Eject. MASTER TEMPO switches to test-operation mode (its LED
lights):

| Command | Key | FL |
|---|---|---|
| Servo All Off | TIME | ALL OFF |
| LD on/off | TEMPO | LD ON / LD OFF |
| Focus on | RELOOP (stopped, LD on) | FCS ON |
| Spindle kick, tracking on/off | IN/CUE | TRK ON / TRK OFF |
| Tracking off | OUT | (printed TRK ON) |
| Slide FWD / REV (about 2 mm) | SEARCH FWD / REV | SLD FWD / SLD REV |
| Pickup up/down | DISPLAY/UTILITY (stopped) | PU UP/DN |
| Back to player mode | MASTER TEMPO | LED off |

Start in steps: Servo All Off, then Focus On, then Spindle Kick/Tracking On.
These FL strings are in the MAIN image's message table (0x0C13E700..).

### 6.4 JOG load (§8.2, p.45-46)

Rotate the jog five times first, enter the JOG load mode, spin it quickly
clockwise. The FL shows speed, time and a judgement. Spec: the time to slow
from ×3 to ×1.5 (shown only if the peak is ×7 or more), averaged over four
spins, 80 ± 10 ms in production (80 ± 25 ms for the finished product). Adjust
the cam plate (-1/-2/-3 lighter, +1/+2 heavier, 0 nominal).

---

## 7. Firmware update and recovery (§8.4, p.47)

- File **C350MAIN.UPD** (device: MAIN CPU; the name carries no version), on
  FAT32 USB memory (not HFS+) or CD-R/RW.
- USB: POWER ON holding SOURCE SELECT/USB + RELOOP until "DOWNLOAD"; the FL
  shows the old and new versions; "COMPLETE" when done, then power off.
- CD: SOURCE SELECT/DISC + RELOOP, otherwise the same.
- Power lost during the update leaves the unit unusable; E-7024 means
  recovery is needed: run the update again, **from USB only**.
- §8.1 (p.45): check the firmware version before any repair; re-run the
  adjustments after replacing the MAIN ASSY (flash) or the jog parts.

---

## 8. User settings (§8.3, p.46)

Stored in IC101 (the flash) as "UTILITY setting" and "status of keys", as on
the CDJ-850. Defaults (printed bold) did not survive extraction: **open**.

| Item | Values |
|---|---|
| CUE LEVEL | -36 … -78 dB in 6 dB steps |
| MIDI CHANNEL | 1-16 |
| AUTO STANDBY | OFF / 20 / 40 / 60 min |
| LIBRARY CREATOR | LIBRARY / FOLDER |
| VERSION No. | (display) |
| TIME MODE | TIME / REMAIN |
| AUTO CUE | ON / OFF |
| JOG MODE | CDJ / VINYL |
| TEMPO RANGE | ±6 / ±10 / ±16 % / WIDE |
| MASTER TEMPO | ON / OFF |

---

## 9. Panel board (PNLB, §10.7, p.68-69)

### 9.1 Panel CPU IC501 pins

| Pin | Net | Source |
|---|---|---|
| 3 | VDET (5 V normal, 0 V fault) | p.30 |
| 4 | POWER_ON | p.19, p.20 |
| 12 | RESET (from IC502 BU4242G; must be H) | p.19, p.27 |
| 18 / 19 / 20 | RENCO_SW / RENC_2 / RENC_1 (rotary selector) | p.17 |
| 25 / 26 | JOG2_C / JOG1_C (via CN501 pins 4/3) | p.23 |
| 27 | TCH_C (jog touch, L when touched) | p.23 |
| 37 | TSCK (link clock, panel drives it) | p.27 |
| 40 | MAIN reset out | p.19 |
| 95 / 97 | TEMPOR / TEMPO (slider, 5.0-0 V) | p.17, p.22 |
| 25-30, 31-46, 47-50, 76-100 | P7, P5/P6 (UARTs), P4 (CS), P1/P0/P10 (analog KI0-KI3, AN) port functions | p.68 |

Other panel nets: KEY0..KEY3 (analog key ladders, 1000 pF each); CNVSS,
DISPRESET, BUSY, SCLK, RXD, TXD on an 8-pin connector row on p.68 (its
designator is cut off in the text); DBG_TxD/DBG_RxD. Which switches sit on which KEY line, and the ladder
resistors, are **open**.

### 9.2 Switches (p.68-69, *order*)

| Ref | Key | Ref | Key |
|---|---|---|---|
| S501 | UTILITY | S513 | USB |
| S502 | T-SEARCH - | S514 | CD |
| S503 | T-SEARCH + | S515 | PC |
| S504 | SEARCH - | S516 | PLAY |
| S505 | SEARCH + | S517 | CUE |
| S506 | EJECT | S518 | LOOP IN |
| S507 | VINYL | S519 | LOOP OUT |
| S508 | MASTER TEMPO | S520 | RELOOP |
| S509 | TEMPO | S521 | LOOP DIVIDE - |
| S510 | TIME | S522 | LOOP DIVIDE + |
| S511 | BACK | S523 | BPM LOCK |
| S512 | PLAYLIST | S524 | USB STOP |
| S525 | STANDBY | S527 | rotary selector (YSD5019) |

### 9.3 LEDs (p.69, *order*)

PLAY green (D504), CUE amber (D505), LOOP IN / LOOP OUT / RELOOP amber
(D506-D508), CD / USB / PC green (D509-D511), EJECT green (D514), BEAT LOOP
amber (D515), VINYL blue (D516), MASTER TEMPO red (D517), STANDBY red /
POWER ON green (D512/D513).

### 9.4 FL tube V501 (DEL1072)

FLSO pin 32, FLSCLK pin 33, FLLAT pin 34, FLBK pin 35 (waveforms 1-4 on p.78,
1 V/div, 100 µs/div); VH pin 17 (28.8-35.2 V), VDD pin 18 (4.5-5.5 V), F+
pin 19 (5.54-5.66 V), F- through R539 (1.98-2.42 V). Mounted with
double-sided tape (§1.3, p.6).

---

## 10. USB (p.15, p.17, p.24, p.29, p.59, p.69)

- **USB-A** (JA505, DKN1620) is on the PNLB. Its data lines reach MAIN over
  CN402 pins 1-2 (USBA_D_P/N). Its VBUS switch IC504 BD2051AFJ is on the
  PNLB too: pin 4 EN = USB_VBUSON, pin 5 /OC = USB_CDETFLG, inputs V+5EV /
  V+5R6, output V+5USB (VBUS 4.75-5.25 V). IC505 NJM2845DL1-05 sits on the
  USB lines (function not stated).
- **USB-B** (JA703, DKN1237) is on the JACK board: CN701 pin 12 USBB_D_N,
  13 USBB_D_P, 15 VBUS_B.
- **IC107** TS3USB30RSW on MAIN switches the CPU's D+/D- between the two,
  select USB_ABSEL. p.59's text reads "Low selects USBA, High selects USBB";
  the CDJ-850 manual has high = A. **Open**: the column order on p.59 is not
  reliable, and the emulator's CDJ-850 model (PB10 high = A) mounts the stick
  under the CDJ-350 firmware.
- Troubleshooting [4-1] (p.24): no USB-A communication: check JA505 pin 1
  V+5USB and IC504 pin 2; IC504 pins 4 and 5 both high means IC504 is bad;
  pin 5 high and pin 4 low with no device means IC103 is bad. [4-2]: USB-B,
  check USB_D+/D-, then IC103.
- PC check (§5.4, p.29): in CONTROL MODE (a key held over 1 s; the key name
  is missing in the text) the PC lists a USB Composite Device, a USB Audio
  Device and an HID-compliant device.

---

## 11. Supplies and monitoring

### 11.1 Rails (§10.10 voltages, p.76)

| Point | Normal (V) | | Point | Normal (V) |
|---|---|---|---|---|
| V+7R6M (SMPS) | 7.22-7.98 | | V+1R25 | 1.24-1.26 |
| V+5EV (SMPS) | 4.75-5.25 | | V+5R6M | 5.54-5.66 |
| V+7R6DCDC (SMPS) | 7.22-7.98 | | V+3R3S | 3.27-3.33 |
| VFDP32 (SMPS) | 29.12-34.88 | | VREF1R65 | 1.57-1.73 |
| V-7R6A / V+7R6A | ∓7.22-7.98 | | V+5A | 4.95-5.05 |
| V+5R6 | 5.53-5.87 | | V-AUDIO | 7.22-7.98 |
| V+3R3 | 3.2-3.4 | | FL VH / VDD / F+ / F- | see 9.4 |
| V+1R2 | 1.19-1.21 | | USB VBUS | 4.75-5.25 |
| V+3R3A | 3.27-3.33 | | | |

CN401 (MAIN, from the SMPS, AKM1299, 13 pins) carries GNDM, POWER_ON, GNDD,
V+5EV, V+7R6DCDC ×2, VFDP32, V-7R6A, V+7R6A, GNDA *(order)*.

### 11.2 Voltage monitoring (§1.2 p.6, §5.5 p.30)

VDET (IC501 pin 3) watches the MAIN and SMPS supplies from 0.1 s after power
on and judges a fault 0.5 s after it appears; the unit then shuts off and
the STANDBY LED flashes. Unplug for a minute before powering on again. To
diagnose, swap R599 into the R569 position on the PNLB (forces power on),
measure, repair, and swap it back.

---

## 12. CPU and DSP basic checks (§5.2 [9], p.27)

| Part | Check | Verdict |
|---|---|---|
| MAIN CPU IC103 | MAIN_RST high, CLK66M, SH_STATUS (pin 85) toggling | SH_STATUS stuck: CPU not running; then IC103, IC101 or IC102 |
| Servo DSP IC201 | SRVRST high, CLK_S_16M, SRVSCLK (IC103 pin 126) | no SRVSCLK with reset and clock OK: IC201 |
| Audio DSP IC301 | DSPRST high, CLK_A_16M, DSPDREQ (IC103 pin 72) | no DSPDREQ with reset and clock OK: IC301 |
| Panel CPU IC501 | RESET (pin 12) high, TSCK (pin 37) | no TSCK with reset OK: IC501 |

Known IC103 pins (p.19, p.77): 50 CLK66M, 59 RES, 69 servo reset out, 72
DSPDREQ, 85 SH_STATUS_TP, 126 SRVSCLK, 134 LPS2, 135 LPS1, 158 DSP reset out
(the same pins as the CDJ-850's IC103). **Open**: the full IC103 pin table.

---

## 13. Drive side connectors (p.14, p.57, p.60, p.80)

- CN201 (MAIN, DKN1404, 50 pins) to the CNCT board: stepper ST1±/ST2±
  (pins 1-8), loading LO± (9-12), LPS1/LPS2 (15/14), pickup A_A..A_D,
  A_S1/A_S2, PD, LD780, TD/TR/FD/FR, spindle U/V/W and HU/HV/HW±, V+5M,
  V+5P, VREF; pin 49 INSIDE (INSW) and pin 50 V+3R3S per p.60 (p.14 puts INSW
  at 50: *order*).
- CN751 (SLMB, 5 pins): 1 LPS2, 2 LPS1, 3 GNDD, 4 LO+, 5 LO-. LPS1/LPS2 go
  low while S751/S752 are pressed (p.24).
- Jog: CN501/CN851 (4 pins) 1 GNDD, 2 V+5, 3 JOG1, 4 JOG2; touch:
  CN504/CN801 (3 pins) 1 GNDD, 2 TCH, 3 V+5.

---

## 14. Not read or not legible

- p.10 (panel facilities drawing): no text.
- IC103 full pin table (MAIN ASSY 1/4 to 4/4 give only the pins above).
- Key ladder: which keys share KEY0..KEY3 and their resistor values.
- Default values of the user settings.
- The CONTROL MODE key name on p.29.
