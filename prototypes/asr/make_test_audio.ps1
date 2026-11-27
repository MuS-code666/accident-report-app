# 音声認識の検証用に、Windows の読み上げ音声（日本語）で架空の文を WAV にする。
# 使い方: powershell -ExecutionPolicy Bypass -File prototypes/asr/make_test_audio.ps1 -OutDir data/asr_test
# 注意: 合成音声は雑音がなく発音も明瞭なので、実際の声より認識しやすい（参考値）。
param([string]$OutDir = "data/asr_test")

Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $OutDir | Out-Null

$sentences = @(
  "きのうの夕方、第一工場の搬出口で、フォークリフトで後退していたらシャッターの柱に当たりました。",
  "カウンターウェイトの左後ろがへこみました。けが人はいません。",
  "選別ラインのコンベヤーを清掃中に、ローラーに右手の人差し指を挟まれました。",
  "油圧ショベルのバケットで破砕機に廃棄物を投入していました。",
  "誘導員は重機の左後ろに立っていて、手で合図をしていました。",
  "搬入口の右側に、禁忌品を分けるためのポリバケツが三つ並んでいました。",
  "ホイールローダーが右に旋回しながら前進して、選別機の角に接触しました。",
  "フレコンをよけようとして、およそ一メートルバックしました。",
  "RPFの原料をトラックに積み込む前に、フレコンの重さを確認しました。",
  "事故のあと、すぐに作業を止めて班長に報告しました。"
)

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq "ja-JP" } | Select-Object -First 1
if (-not $voice) { throw "日本語の読み上げ音声が見つかりません" }
$synth.SelectVoice($voice.VoiceInfo.Name)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

$i = 1
foreach ($s in $sentences) {
  $name = "{0:D2}" -f $i
  $synth.SetOutputToWaveFile((Join-Path $OutDir "$name.wav"), $fmt)
  $synth.Speak($s)
  $synth.SetOutputToNull()
  [System.IO.File]::WriteAllText((Join-Path $OutDir "$name.txt"), $s, (New-Object System.Text.UTF8Encoding $false))
  $i++
}
$synth.Dispose()
Write-Output "作成: $($sentences.Count) 件 → $OutDir"
