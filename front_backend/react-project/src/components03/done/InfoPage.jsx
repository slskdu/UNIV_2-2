// 1~4. 입력란 상태 관리, 엔터키 이벤트, 그리고 useRef를 활용한 포커스 이동 실습입니다.
import React, { useRef, useState } from 'react' // 5. 상태 관리를 위한 useState와 DOM 접근을 위한 useRef를 불러옵니다.
import '../Style03.css'

const InfoPage = () => {
    // 8~9. 이름(문자열)과 나이(숫자)를 관리할 상태 변수를 만듭니다.
    const [name, setName] = useState('');
    const [age, setAge] = useState(0);
    
    // 10. [핵심 포인트] 특정 태그(여기서는 이름 입력상자)를 가리키기 위한 이름표(ref)를 만듭니다.
    // 초기값은 null(비어있음)로 설정합니다.
    const nameRef = useRef(null);

    // 12. 확인(등록) 버튼을 눌렀을 때 실행될 함수입니다.
    const onClick = () => {
        // 13. 논리 연산자(!, ||)를 사용한 유효성 검사입니다.
        // name이 비어있거나(falsy) 또는(||) age가 0/비어있으면 경고창을 띄웁니다.
        if(!name || !age){
            alert('이름또는 나이를 입력하세요!');
        } else {
            alert(`${name}, ${age} 등록완료!`);
        }
        
        // 18. [핵심 포인트] 경고창이 꺼진 후, 커서를 다시 '이름' 입력상자로 이동시킵니다.
        // useRef로 만든 변수 뒤에 반드시 '.current'를 붙여야 실제 태그에 접근할 수 있습니다.
        nameRef.current.focus();
    }

    // 21. 입력상자 안에서 키보드를 눌렀을 때 실행되는 함수입니다.
    const onKeyDown = (e) => { 
        // 22. 누른 키가 'Enter'라면, 위에 만들어둔 onClick() 함수를 똑같이 실행합니다.
        if(e.key === 'Enter') onClick(); 
    }
    
    return (
        <div className='box'>
            {/* 27. name이나 age 값이 없을 경우 OR 연산자(||)를 통해 기본값 '?'를 출력합니다. */}
            <h3>이름:{name || '?'} | 나이:{age || '?'}</h3>
            
            {/* 28~30. 이름 입력상자 
                - ref={nameRef}: 위에서 만든 nameRef가 이 <input> 태그를 가리키도록 연결합니다.
                - onChange: 값이 변할 때마다 e.target.value로 현재 값을 가져와 setName으로 상태를 업데이트합니다. */}
            <input ref={nameRef} value={name} 
                onChange={(e)=>setName(e.target.value)} onKeyDown={onKeyDown}
                placeholder='이름'/><br/>
                
            {/* 31~33. 나이 입력상자 
                (참고: setp={1}은 step={1}의 오타인 것 같습니다. 1씩 증감한다는 뜻입니다.) */}
            <input value={age} 
                onChange={(e)=>setAge(e.target.value)} onKeyDown={onKeyDown}
                placeholder='나이' type='number' step={1}/><br/>
                
            <button onClick={onClick}>등록</button>
        </div>
    )
}
export default InfoPage