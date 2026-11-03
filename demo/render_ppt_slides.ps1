param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$office = $null
$presentation = $null
$startedEmpty = $false
try {
    $office = New-Object -ComObject PowerPoint.Application
    $startedEmpty = ($office.Presentations.Count -eq 0)
    $presentation = $office.Presentations.Open($config.source, -1, 0, 0)
    $ratio = $presentation.PageSetup.SlideHeight / $presentation.PageSetup.SlideWidth
    foreach ($page in $config.pages) {
        $target = Join-Path $config.output "slide-$page.png"
        $slide = $presentation.Slides.Item([int]$page)
        $slide.Export($target, 'PNG', 1440, [int](1440 * $ratio))
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($slide)
    }
} finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)
    }
    if ($null -ne $office) {
        # Never quit an Office session containing the user's presentations.
        if ($startedEmpty -and $office.Presentations.Count -eq 0) { $office.Quit() }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($office)
    }
}
