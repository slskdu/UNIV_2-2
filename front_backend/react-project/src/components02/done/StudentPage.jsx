// 1. 부모 컴포넌트에서 학번, 성명, 학과 데이터를 자식 컴포넌트로 전달하는 예제입니다.
import '../Style02.css'
import Student from './Student' // 3. 데이터를 받아 화면에 그려줄 자식 컴포넌트 'Student'를 불러옵니다.

// 5~9. 화면에 출력할 학생 데이터(객체)들이 담긴 배열입니다.
const students = [
    { id: 1, name: '홍길동', dept: '컴정과' },
    { id: 2, name: '심청이', dept: '전자과' },
    { id: 3, name: '이순신', dept: '건축과' },
]

const StudentPage = () => {
    return (
        <div className='box'>
            <h1>학생목록</h1>
            {/* 14. 자바스크립트의 map() 함수를 사용해 배열(students) 안의 데이터 개수만큼 반복해서 태그를 그려냅니다. 
                여기서 'student'는 배열 안의 객체를 하나씩 순서대로 꺼내 담는 임시 변수입니다. */}
            {students.map(student=>
                // 15. 불러온 자식 컴포넌트(<Student />)에 'student'라는 이름의 이름표(prop)를 달아 데이터를 전달합니다.
                // [매우 중요] 현재 코드에는 빠져있지만, map()으로 컴포넌트를 반복 생성할 때는 리액트가 각각을 구분할 수 있도록 반드시 고유한 'key' 속성을 넣어주어야 경고 에러가 나지 않습니다.
                <Student student={student}/>
            )}
        </div>
    )
}
export default StudentPage