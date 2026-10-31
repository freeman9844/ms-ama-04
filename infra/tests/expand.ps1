<#
.SYNOPSIS
  Bicep 템플릿을 .bicepparam 값으로 펼쳐(PSRule for Azure) 리소스 JSON 배열로 저장합니다.
  tests/infra 의 pytest fixture가 호출합니다.
#>
param(
    [Parameter(Mandatory)][string]$TemplateFile,
    [Parameter(Mandatory)][string]$ParamFile,
    [Parameter(Mandatory)][string]$OutFile
)
$ErrorActionPreference = 'Stop'
Import-Module PSRule.Rules.Azure -ErrorAction Stop
$bicep = if ($env:BICEP_PATH) { $env:BICEP_PATH } else { Join-Path $HOME '.azure/bin/bicep.exe' }
$work = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $template = Join-Path $work 'main.json'
    $parameters = Join-Path $work 'parameters.json'
    & $bicep build $TemplateFile --outfile $template
    if ($LASTEXITCODE -ne 0) { throw "bicep build 실패: $TemplateFile" }
    & $bicep build-params $ParamFile --outfile $parameters
    if ($LASTEXITCODE -ne 0) { throw "bicep build-params 실패: $ParamFile" }
    $resources = Export-AzRuleTemplateData -TemplateFile $template -ParameterFile $parameters -PassThru
    $resources | ConvertTo-Json -Depth 100 -AsArray | Set-Content -Path $OutFile -Encoding utf8
}
finally {
    Remove-Item -Recurse -Force $work
}
