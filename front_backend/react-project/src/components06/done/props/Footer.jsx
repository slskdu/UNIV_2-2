// 1. 최상위 부모(RootPage)에서 만든 다크모드 상태(dark)와 상태 변경 함수(setDark)를 모두 받습니다.
const Footer = ({dark, setDark}) => {
    return (
        // 3. 앞선 Header와 완벽히 동일한 원리입니다. (템플릿 리터럴 + 삼항 연산자)
        // dark가 true면 'footer black', false면 'footer gray' 클래스가 적용됩니다.
        <div className={`footer ${dark ? 'black':'gray'}`}>
            
            {/* 4. [초핵심] 상태 반전(Toggle)과 동적 텍스트 출력
                - onClick: 버튼을 클릭하면 setDark 함수를 실행합니다. 
                  이때 현재 dark 값의 반대값(!dark)을 넣어줍니다. (true면 false로, false면 true로)
                - 텍스트: 현재 다크모드(true)라면 화면을 하얗게 돌릴 수 있도록 'Light Mode'라는 글자를 보여주고, 
                  다크모드가 아니라면(false) 'Dark Mode'라는 글자를 보여줍니다. */}
            <button onClick={()=>setDark(!dark)}>{dark ? 'Light Mode' :'Dark Mode'}</button>
        </div>
    )
}
export default Footer