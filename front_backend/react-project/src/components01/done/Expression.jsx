// 1. JSX에서 자바스크립트 표현식을 구성하는 변수, 연산자(AND, OR, 삼항, 스프레드) 사용방법을 알아보자.
import '../Style01.css' // 2. 외부 CSS 파일을 불러옵니다.

// 4. 화살표 함수 형태로 'Expression' 이라는 React 컴포넌트를 선언합니다.
const Expression = () => {
    // 5~8. 컴포넌트 내부에서 사용할 일반 변수들을 선언합니다.
    const name = 'Chris';
    const age = 20;
    const job = 'Student';
    const isLogin = true; 

    // 9. 객체 초기자(Shorthand property names) 방식: {name: name, age: age}와 동일합니다.
    const student = {name, age} 
    
    // 10. 스프레드 연산자(...): 기존 student 객체의 속성들을 모두 풀어서 넣고, job 속성을 추가해 새로운 객체(person)를 만듭니다.
    const person = {...student, job}

    return (
        // 13. JSX에서 HTML의 class 속성은 반드시 'className'으로 작성해야 합니다.
        <div className='box'>
            {/* 14. JSX 내부에서의 주석 작성 방법입니다. 반드시 {/* ... */} 형태를 써야 합니다. */}
            
            <h1>변수값 출력</h1>
            {/* 16. 
                - {name || 'Justin'}: name이 거짓 같은 값(falsy)이면 'Justin'을 출력 (OR 연산자)
                - {age+10}: 중괄호 안에서는 수학 연산도 가능합니다.
                - {isLogin.toString()}: 리액트에서 boolean 값(true/false)은 화면에 그냥 출력되지 않으므로 문자로 변환해줍니다.
            */}
            <h3>{name || 'Justin'}, {age+10}, {job || 'Programer'}, {isLogin.toString()}</h3>
            
            {/* 17. 백틱(`)을 이용한 템플릿 리터럴 문법입니다. 자바스크립트 문자열 안에 변수를 쉽게 넣을 수 있습니다. */}
            <h3>{`Name:${name}, Age:${age}, Job:${job}`}</h3>

            <h1>삼항 연산자</h1>
            {/* 20. 조건 ? 참일 때 렌더링할 요소 : 거짓일 때 렌더링할 요소 (조건부 렌더링의 핵심) */}
            <h3>{isLogin ? <button>Logout</button>:<button>Login</button>}</h3>

            <h1>AND 연산자</h1>
            {/* 23. isLogin이 참(true)일 때만 뒤의 <div> 태그 내용이 렌더링됩니다. */}
            <h3>{isLogin && <div>{name || 'Justin'}, {age || 0}, {job || 'Programer'}</div>}</h3>
            {/* 24. !isLogin (로그인 상태가 false일 때) 참이 되어 'Please Login!'이 출력됩니다. */}
            <h3>{!isLogin && <div>Please Login!</div>}</h3>

            <h1>OR 연산자</h1>
            {/* 27~28. 변수 값이 없을 때(null, undefined, 0, '' 등) 뒤에 있는 기본값을 출력하는 역할을 합니다. */}
            <h3>{age || 0}</h3>
            <h3>{job || 'Programer'}</h3>

            <h1>스프레드 연산자</h1>
            {/* 31. 앞서 스프레드 연산자로 만든 person 객체의 속성(name, age, job)에 접근하여 출력합니다. */}
            <h3>{person.name || 'Justin'}, {person.age || 0}, {person.job || 'Programer'}</h3>
        </div>
    )
}
// 35. 작성한 컴포넌트를 외부에서 사용할 수 있도록 내보냅니다.
export default Expression