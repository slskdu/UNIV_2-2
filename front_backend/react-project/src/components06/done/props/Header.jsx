// 1. 부모(Page)로부터 전달받은 다크모드 상태 값(dark)을 구조 분해 할당으로 받습니다.
const Header = ({dark}) => {
    return (
        // 3. [초핵심] 동적 클래스명 부여 (템플릿 리터럴 + 삼항 연산자)
        // 자바스크립트 변수와 문자열을 섞어 쓰기 위해 백틱(`) 기호를 사용했습니다.
        // 무조건 들어가는 기본 클래스명인 'header'를 적고 한 칸 띄운 뒤,
        // ${ } 안에서 삼항 연산자를 돌립니다: "dark 값이 참(true)이면 'black'을, 거짓(false)이면 'gray'를 클래스명으로 추가해라!"
        // 결과적으로 <div className="header black"> 또는 <div className="header gray"> 가 됩니다.
        <div className={`header ${dark ? 'black':'gray'}`}>
            <h1>Welcom 사용자</h1> {/* (참고: Welcome의 오타인 듯하지만 그대로 출력됩니다 ㅎㅎ) */}
        </div>
    )
}
export default Header // 7. 외부에서 사용할 수 있게 내보냅니다.