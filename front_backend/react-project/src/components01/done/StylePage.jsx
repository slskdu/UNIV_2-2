// 1. JSX 요소에 직접 스타일을 적용하는 인라인 방식과 className으로 스타일을 적용하는 방법을 배웁니다.
import '../Style01.css' // 2. 외부에 미리 작성해둔 CSS 파일을 불러옵니다(import).

// 4. StylePage라는 이름의 리액트 컴포넌트를 생성합니다.
const StylePage = () => {
    // 5~9. 인라인 스타일을 위한 자바스크립트 '객체(Object)'를 미리 만들어 둡니다.
    // ※ 주의: 리액트에서 CSS 속성 이름은 반드시 카멜표기법(camelCase)으로 작성해야 합니다!
    // (예: font-size -> fontSize, font-family -> fontFamily)
    const style = {
        fontSize:'30px',   // 폰트 크기
        fontFamily:'돋움체', // 폰트 종류
        color:'red',       // 글자 색상
    }
    
    return (
        // 12. 외부 CSS 파일(Style01.css)에 정의된 '.box' 스타일을 적용합니다. (class 대신 className 사용)
        <div className='box'>
            {/* 13. 마찬가지로 외부 CSS의 '.hello' 스타일을 적용합니다. */}
            <h1 className='hello'>안녕 리액트!</h1>
            
            {/* 14. 태그 안에 직접 스타일 객체를 집어넣는 '인라인(Inline)' 방식입니다.
                바깥쪽 중괄호 { }는 "지금부터 자바스크립트 쓸게!"라는 뜻이고,
                안쪽 중괄호 { }는 "이거 자바스크립트 객체야!"라는 뜻입니다.
                그래서 괄호가 두 개 {{ }} 겹쳐서 사용됩니다. */}
            <h2 style={{fontSize:'50px',fontFamily:'궁서체',color:'blue'}}>저는 홍길동입니다.</h2>
            
            {/* 15. 위(5~9번 줄)에서 미리 만들어둔 style 객체를 변수로 불러와서 적용합니다. 
                이미 만들어진 객체(변수)를 넣는 것이므로 중괄호 { }를 한 번만 씁니다. */}
            <h3 style={style}>만나서 반갑습니다.</h3>
        </div>
    )
}

// 19. 외부에서 이 컴포넌트를 사용할 수 있도록 내보냅니다.
export default StylePage