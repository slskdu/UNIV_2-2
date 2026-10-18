// 1~2. 텍스트 내용(message)과 색상(color) 상태를 변경하는 실습입니다.
import React, { useState } from 'react' // 3. 상태 관리를 위해 useState 훅을 불러옵니다.
import '../Style02.css'

const SayPage = () => {
    // 7. 초기 메시지로 사용할 문자열을 변수에 담아둡니다. (그냥 useState 안에 직접 적어도 무방합니다)
    const initMessage = 'Please press the button!'
    
    // 8. 텍스트 내용을 관리할 상태입니다. 초기값은 위에서 만든 initMessage가 됩니다.
    const [message, setMessage] = useState(initMessage);
    
    // 9. 텍스트 색상을 관리할 상태입니다. 초기 색상은 'black'으로 설정합니다.
    const [color, setColor] = useState('black');

    return (
        <div className='box'>
            {/* 13. 인라인 스타일을 적용합니다. 
                바깥 { }는 자바스크립트를 쓰겠다는 뜻이고, 안쪽 { }는 객체를 의미합니다.
                {color: color} 에서 앞의 color는 CSS 속성명, 뒤의 color는 우리가 만든 상태 변수입니다. */}
            <h1 style={{color:color}}>{message}</h1>
            
            <div>
                {/* 16. [매우 중요] 리액트에서 태그 안에 직접 함수를 넣을 때는 반드시 '() => 함수명()' 형태의 화살표 함수(콜백 함수)로 감싸주어야 합니다. 
                    onClick={setMessage('Hello!')} 라고 괄호 없이 직접 실행해버리면 
                    버튼을 누르기도 전에 화면이 그려지자마자 함수가 실행되어 무한 에러가 발생합니다. */}
                <button onClick={()=>setMessage('Hello!')}>입장</button>
                <button onClick={()=>setMessage('Good bye!')}>퇴장</button>
            </div>
            
            <div>
                {/* 20~22. 마찬가지로 콜백 함수 형태(() => setColor())를 사용하여 각각의 버튼을 누를 때마다 color 상태 값을 변경합니다. 
                    상태 값이 바뀌면 화면이 리렌더링되면서 13번 줄의 <h1> 태그 색상이 즉시 변경됩니다. */}
                <button onClick={()=>setColor('red')}>빨강색</button>
                <button onClick={()=>setColor('green')}>초록색</button>
                <button onClick={()=>setColor('blue')}>파랑색</button>
            </div>
        </div>
    )
}
export default SayPage