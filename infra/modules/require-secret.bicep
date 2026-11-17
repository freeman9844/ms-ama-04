// 리소스 없는 검사용 모듈입니다. 값이 32자보다 짧으면 ARM 파라미터 검증에서 배포가 멈춥니다.
@secure()
@minLength(32)
#disable-next-line no-unused-params
param value string
