// 1. 부모(Page)로부터 'dark' 상태(true 또는 false)를 넘겨받습니다.
const Content = ({dark}) => {
    return (
        // 3. [복습 포인트] 템플릿 리터럴(` `)과 삼항 연산자(? :)의 결합입니다.
        // 앞선 Header나 Footer에서는 거짓일 때 'gray'를 썼지만, 
        // 본문(Content) 영역은 다크모드가 아닐 때(false) 바탕을 하얗게 만들기 위해 'white'를 적용했습니다.
        // 결과: 다크모드 켜짐 -> <div className="content black">
        //       다크모드 꺼짐 -> <div className="content white">
        <div className={`content ${dark ? 'black':'white'}`}>
            <h1>사용자님 좋은 하루되세요!</h1>
        </div>
    )
}
export default Content // 8. 외부에서 이 컴포넌트를 쓸 수 있게 내보냅니다.