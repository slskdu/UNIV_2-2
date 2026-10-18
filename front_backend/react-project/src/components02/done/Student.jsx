// 1. 부모가 넘겨준 'student'라는 이름의 데이터를 Props로 받아오는 자식 컴포넌트입니다.
// [중요 포인트 1] ( props ) 로 받아서 안에서 props.student 로 써도 되지만, 
// 처음부터 중괄호를 써서 ({ student }) 로 받으면 코드가 훨씬 깔끔해집니다.
const Student = ({student}) => {
    
    // 3. [중요 포인트 2] 객체 구조 분해 할당 (Destructuring)
    // student 안에는 { id: 1, name: '홍길동', dept: '컴정과' } 객체가 들어있습니다.
    // 매번 student.id, student.name 이라고 쓰기 귀찮기 때문에,
    // 중괄호 { } 를 이용해 객체 안의 속성들을 꺼내서 각각의 변수(id, name, dept)에 한 번에 담아버리는 문법입니다.
    const {id, name, dept} = student;
    
    return (
        // 5. 컴포넌트는 반드시 하나의 최상위 태그(여기서는 <div>)로 감싸져 있어야 합니다.
        // 주석에서 언급된 Fragment(<>)를 쓰면 <div> 대신 화면에 태그를 남기지 않고 묶을 수 있습니다.
        <div>
            {/* 6~8. 위에서 구조 분해 할당으로 꺼내둔 변수들을 화면에 출력합니다. */}
            <h3>학번:{id}</h3>
            <h3>이름:{name}</h3>
            <h3>학과:{dept}</h3>
            <br/>
        </div>
    )
}
export default Student // 13. 컴포넌트를 외부로 내보냅니다.